from types import SimpleNamespace
from uuid import uuid4

from lead_agent.application.mailbox_access import can_use_mailbox, connection_owner_id
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext


def _workspace(role: str, user_id: str) -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=uuid4(),
        user_id=user_id,
        actor_id=user_id,
        membership_id=uuid4(),
        role=role,
        permissions=role_permissions(role),
    )


def test_member_can_only_use_their_owned_mailbox():
    owner = str(uuid4())
    other = str(uuid4())
    connection = SimpleNamespace(connection_metadata={"owner_user_id": owner})
    assert can_use_mailbox(_workspace("sales_rep", owner), connection)
    assert not can_use_mailbox(_workspace("sales_rep", other), connection)


def test_legacy_oauth_owner_is_supported_but_unowned_mailbox_fails_closed():
    owner = str(uuid4())
    assert connection_owner_id(
        SimpleNamespace(connection_metadata={"oauth_actor_id": owner})
    ) == owner
    assert connection_owner_id(SimpleNamespace(connection_metadata={})) is None
    assert not can_use_mailbox(
        _workspace("viewer", str(uuid4())),
        SimpleNamespace(connection_metadata={}),
    )


def test_workspace_admins_cannot_read_another_members_personal_mailbox():
    mailbox_owner = str(uuid4())
    connection = SimpleNamespace(connection_metadata={"owner_user_id": mailbox_owner})
    assert not can_use_mailbox(_workspace("owner", str(uuid4())), connection)
    assert not can_use_mailbox(_workspace("admin", str(uuid4())), connection)
    assert can_use_mailbox(_workspace("owner", mailbox_owner), connection)


def test_first_class_owner_column_cannot_be_overridden_by_metadata():
    owner = uuid4()
    attacker = uuid4()
    connection = SimpleNamespace(
        owner_user_id=owner,
        connection_metadata={"owner_user_id": str(attacker)},
    )
    assert connection_owner_id(connection) == str(owner)
    assert can_use_mailbox(_workspace("owner", str(owner)), connection)
    assert not can_use_mailbox(_workspace("admin", str(attacker)), connection)
