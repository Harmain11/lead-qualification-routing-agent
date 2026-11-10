import pytest
from fastapi import HTTPException
from test_team_scope import store  # noqa: F401

from lead_agent.application.lead_access import assert_lead_action
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.teams import WorkspaceTeamLeadORM


@pytest.mark.parametrize("action", ["reassign", "delete_restore"])
def test_action_specific_scope_override_does_not_fall_back_to_edit_scope(store, action):  # noqa: F811
    session, context, visible, *_ = store
    manager = WorkspaceContext(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        actor_id=context.actor_id,
        membership_id=context.membership_id,
        role="sales_manager",
        permissions=role_permissions("sales_manager") | {f"leads.{action}"},
        access_policy={
            "scopes": {
                "leads.edit": "team",
                f"leads.{action}": "none",
            },
        },
    )

    with pytest.raises(HTTPException, match="outside your authorized scope"):
        assert_lead_action(session, manager, visible, action)


def test_manager_action_scope_accepts_any_explicitly_linked_managed_team(store):  # noqa: F811
    session, context, _visible, hidden, _foreign, team, _teammate = store
    session.add(WorkspaceTeamLeadORM(
        workspace_id=context.workspace_id,
        team_id=team.id,
        lead_id=hidden.id,
    ))
    session.commit()
    manager = WorkspaceContext(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        actor_id=context.actor_id,
        membership_id=context.membership_id,
        role="sales_manager",
        permissions=role_permissions("sales_manager"),
    )

    assert_lead_action(session, manager, hidden, "view")
