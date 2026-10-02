from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies, get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.icp import (
    create_draft,
    get_version,
    list_versions,
    preview_draft,
    publish_draft,
    resolve_workspace_icp,
    update_draft,
    validate_draft,
)
from lead_agent.application.services import process_lead
from lead_agent.domain.configuration import IcpConfig, RoutingConfig, WorkspaceIcpDraftConfig
from lead_agent.domain.rbac import effective_permissions, role_permissions
from lead_agent.domain.scoring import qualify_lead
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    QualificationResultORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.session import get_db


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _workspace(session: Session, name: str = "ICP test") -> WorkspaceORM:
    now = datetime.now(UTC)
    row = WorkspaceORM(
        id=uuid4(),
        name=name,
        slug=f"icp-{uuid4().hex}",
        status="active",
        is_demo=False,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def _payload(external_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "website",
        "source_detail": "icp-test",
        "external_id": external_id,
        "full_name": "Sarah Malik",
        "email": f"{external_id}@example.test",
        "phone": "+1 555 0100",
        "job_title": "VP of Operations",
        "company_name": "Northstar SaaS",
        "industry": "SaaS",
        "employee_count": 180,
        "annual_revenue": 2_000_000,
        "geography": "United States",
        "budget": 30_000,
        "product_or_service": "Lead routing automation",
        "message": (
            "We are ready to implement lead routing automation this month. We need a proposal, "
            "workflow integration and demo this week. Our budget is $30,000 because missed "
            "leads and slow response are creating a serious problem."
        ),
        "received_at": datetime.now(UTC).isoformat(),
    }
    payload.update(overrides)
    return payload


def _draft_config(icp: IcpConfig, **changes: object) -> dict[str, object]:
    payload = icp.model_dump(mode="json")
    payload.update(changes)
    return payload


def test_default_icp_fallback_is_explicit_and_traceable(
    session: Session, icp: IcpConfig
) -> None:
    workspace = _workspace(session)
    resolved = resolve_workspace_icp(session, workspace.id, icp)
    assert resolved.source == "system_default"
    assert resolved.version_id is None
    assert resolved.configuration.profile_version == icp.profile_version
    assert len(resolved.configuration_hash) == 64


@pytest.mark.parametrize("total", [38, 43])
def test_under_and_over_weight_drafts_are_saved_but_not_publishable(
    session: Session, icp: IcpConfig, total: int
) -> None:
    workspace = _workspace(session)
    configuration = _draft_config(icp)
    configuration["scoring"]["component_points"]["icp_fit"] = (  # type: ignore[index]
        {"industry_match": total}
        if total <= 40
        else {"industry_match": 22, "company_size_fit": total - 22}
    )
    draft = create_draft(
        session,
        workspace.id,
        "owner",
        None,
        icp,
        configuration=configuration,
    )
    parsed, problems = validate_draft(session, workspace.id, draft.id, None)
    assert sum(parsed.scoring.component_points["icp_fit"].values()) == total
    assert problems and "exactly 40" in problems[0]
    with pytest.raises(ValueError, match="cannot be published"):
        publish_draft(session, workspace.id, draft.id, "owner", None)


def test_ranges_conflicts_and_malformed_options_fail_closed(icp: IcpConfig) -> None:
    invalid_range = _draft_config(icp)
    invalid_range["company_size"]["employees_min"] = 500  # type: ignore[index]
    invalid_range["company_size"]["employees_max"] = 50  # type: ignore[index]
    with pytest.raises(ValueError, match="employees_min"):
        WorkspaceIcpDraftConfig.model_validate(invalid_range)
    invalid_rule = _draft_config(icp)
    invalid_rule["disqualifiers"][0]["field"] = "social_security_number"  # type: ignore[index]
    with pytest.raises(ValueError):
        WorkspaceIcpDraftConfig.model_validate(invalid_rule)


def test_published_versions_are_immutable_and_editing_uses_a_new_draft(
    session: Session, icp: IcpConfig
) -> None:
    workspace = _workspace(session)
    first = create_draft(session, workspace.id, "owner", None, icp)
    published = publish_draft(session, workspace.id, first.id, "owner", None)
    with pytest.raises(ValueError, match="immutable"):
        update_draft(session, workspace.id, published.id, None, name="Mutated")
    second = create_draft(
        session,
        workspace.id,
        "owner",
        None,
        icp,
        based_on_version_id=published.id,
    )
    assert second.version_number == 2
    assert second.status == "draft"
    edited = update_draft(
        session,
        workspace.id,
        second.id,
        None,
        name="Enterprise ICP",
        description="Edited draft",
    )
    assert edited.name == "Enterprise ICP"
    assert edited.description == "Edited draft"


def test_publish_archives_prior_active_version_and_audits_operations(
    session: Session, icp: IcpConfig
) -> None:
    workspace = _workspace(session)
    first = create_draft(session, workspace.id, "owner", None, icp)
    publish_draft(session, workspace.id, first.id, "owner", None)
    second = create_draft(session, workspace.id, "owner", None, icp)
    publish_draft(session, workspace.id, second.id, "owner", None)
    session.refresh(first)
    assert first.status == "archived"
    assert resolve_workspace_icp(session, workspace.id, icp).version_id == second.id
    operations = list(
        session.scalars(
            select(WorkspaceAdminAuditEventORM.operation).where(
                WorkspaceAdminAuditEventORM.workspace_id == workspace.id
            )
        )
    )
    assert operations.count("icp.draft_created") == 2
    assert operations.count("icp.published") == 2


def test_pipeline_uses_workspace_icp_and_historical_version_is_retained(
    session: Session, icp: IcpConfig, routing: RoutingConfig
) -> None:
    workspace = _workspace(session)
    v1 = create_draft(session, workspace.id, "owner", None, icp)
    publish_draft(session, workspace.id, v1.id, "owner", None)
    _, first_result, first_lead = process_lead(
        session, _payload("first"), icp, routing, workspace_id=workspace.id
    )
    assert first_result.icp_version_id == v1.id
    assert first_result.icp_source == "workspace"

    config = icp.model_dump(mode="json")
    config["target_industries"] = ["Healthcare"]
    v2 = create_draft(
        session, workspace.id, "owner", None, icp, configuration=config
    )
    publish_draft(session, workspace.id, v2.id, "owner", None)
    _, second_result, _ = process_lead(
        session, _payload("second"), icp, routing, workspace_id=workspace.id
    )
    stored_first = session.scalar(
        select(QualificationResultORM).where(QualificationResultORM.lead_id == first_lead.id)
    )
    assert stored_first is not None and stored_first.icp_version_id == v1.id
    assert second_result.icp_version_id == v2.id
    assert second_result.component_scores["icp_fit"] < first_result.component_scores["icp_fit"]


def test_missing_values_receive_no_configured_fit_points(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    from dataclasses import replace

    from lead_agent.domain.normalization import normalize_lead

    lead = normalize_lead(
        _payload(
            "missing",
            industry=None,
            employee_count=None,
            geography=None,
            job_title=None,
            budget=None,
        ),
        uuid4(),
    )
    lead = replace(
        lead,
        industry=None,
        employee_count=None,
        geography=None,
        job_title=None,
        budget=None,
    )
    result = qualify_lead(lead, icp, routing)
    by_key = {line.key: line for line in result.score_breakdown}
    for key in (
        "industry_match",
        "company_size_fit",
        "geography_match",
        "buyer_role_match",
        "budget_fit",
    ):
        assert by_key[key].points == 0


def test_hard_disqualifier_records_rule_evidence_and_icp_version(
    session: Session, icp: IcpConfig, routing: RoutingConfig
) -> None:
    workspace = _workspace(session)
    draft = create_draft(session, workspace.id, "owner", None, icp)
    publish_draft(session, workspace.id, draft.id, "owner", None)
    _, result, _ = process_lead(
        session,
        _payload("student", job_title="Student", message="This is a school project"),
        icp,
        routing,
        workspace_id=workspace.id,
    )
    assert result.hard_disqualified
    assert result.disqualifier_code == "student_or_hobbyist"
    assert result.disqualifier_evidence
    assert result.icp_version_id == draft.id


def test_preview_is_non_destructive_and_strictly_workspace_scoped(
    session: Session, icp: IcpConfig, routing: RoutingConfig
) -> None:
    workspace_a = _workspace(session, "A")
    workspace_b = _workspace(session, "B")
    _, _, lead_a = process_lead(
        session, _payload("workspace-a"), icp, routing, workspace_id=workspace_a.id
    )
    process_lead(session, _payload("workspace-b"), icp, routing, workspace_id=workspace_b.id)
    draft = create_draft(session, workspace_a.id, "owner", None, icp)
    before_count = session.scalar(select(func.count()).select_from(QualificationResultORM))
    before_id = lead_a.qualification.id if lead_a.qualification else None
    result = preview_draft(session, workspace_a.id, draft.id, routing, None)
    after_count = session.scalar(select(func.count()).select_from(QualificationResultORM))
    assert result["leads_evaluated"] == 1
    assert before_count == after_count
    session.refresh(lead_a)
    assert lead_a.qualification is not None and lead_a.qualification.id == before_id
    assert get_version(session, workspace_b.id, draft.id) is None
    assert list_versions(session, workspace_b.id) == []


def test_icp_rbac_owner_admin_viewer_and_explicit_overrides() -> None:
    assert "icp.manage" in role_permissions("owner")
    assert "icp.manage" in role_permissions("admin")
    assert "icp.manage" not in role_permissions("sales_manager")
    assert "icp.view" in role_permissions("viewer")
    assert "icp.manage" in effective_permissions(
        "sales_manager", {"grants": ["icp.manage"], "denies": []}
    )
    assert "icp.manage" not in effective_permissions(
        "admin", {"grants": [], "denies": ["icp.manage"]}
    )


def test_icp_api_enforces_view_manage_and_cross_workspace_isolation(
    tmp_path, icp: IcpConfig, routing: RoutingConfig
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'icp-api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_a = _workspace(session, "API A")
    workspace_b = _workspace(session, "API B")
    active_context = {
        "value": WorkspaceContext(
            workspace_id=workspace_a.id,
            role="viewer",
            permissions=frozenset({"icp.view"}),
        )
    }

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_policies] = lambda: (icp, routing)
    app.dependency_overrides[get_workspace_context] = lambda: active_context["value"]
    try:
        with TestClient(app) as client:
            assert client.get("/v1/icp/current").status_code == 200
            default = client.get("/v1/icp/default")
            assert default.status_code == 200
            assert default.json()["source"] == "system_default"
            assert default.json()["configuration"]["profile_name"] == icp.profile_name
            assert client.post("/v1/icp/drafts", json={}).status_code == 403

            active_context["value"] = WorkspaceContext(
                workspace_id=workspace_a.id,
                role="admin",
                permissions=frozenset({"icp.view", "icp.manage"}),
            )
            created = client.post("/v1/icp/drafts", json={})
            assert created.status_code == 201
            version_id = created.json()["id"]

            active_context["value"] = WorkspaceContext(
                workspace_id=workspace_b.id,
                role="viewer",
                permissions=frozenset({"icp.view"}),
            )
            assert client.get(f"/v1/icp/versions/{version_id}").status_code == 404

            active_context["value"] = WorkspaceContext(
                workspace_id=workspace_a.id,
                role="admin",
                permissions=frozenset({"icp.view"}),
            )
            assert client.patch(
                f"/v1/icp/versions/{version_id}", json={"name": "Denied"}
            ).status_code == 403
    finally:
        app.dependency_overrides.clear()
        session.close()
