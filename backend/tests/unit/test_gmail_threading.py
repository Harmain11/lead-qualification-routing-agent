import base64
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest

from lead_agent.api.routes.follow_up import FollowUpEdit
from lead_agent.application.gmail_threading import matches_lead_thread, same_gmail_subject
from lead_agent.infrastructure.gmail import GmailOutboundProvider


def test_reply_requires_same_mailbox_thread_reference_and_subject() -> None:
    details = [{
        "gmail_connection_id": "mailbox-a", "thread_id": "thread-a",
        "rfc_message_id": "<source@example.com>", "subject": "Quick meeting",
    }]
    assert same_gmail_subject("Re: Quick meeting", "Quick meeting")
    assert matches_lead_thread(
        details, connection_id="mailbox-a", thread_id="thread-a",
        in_reply_to="<source@example.com>", subject="Re: Quick meeting",
    )
    assert not matches_lead_thread(
        details, connection_id="mailbox-b", thread_id="thread-a",
        in_reply_to="<source@example.com>", subject="Re: Quick meeting",
    )
    assert not matches_lead_thread(
        details, connection_id="mailbox-a", thread_id="thread-a",
        in_reply_to="<source@example.com>", subject="Different subject",
    )
    assert not matches_lead_thread(
        details, connection_id="mailbox-a", thread_id="thread-a",
        in_reply_to="<different@example.com>", subject="Quick meeting",
    )


def test_appointment_edit_validates_attachments_and_recipients() -> None:
    attachment = {
        "filename": "offer.pdf", "content_type": "application/pdf",
        "content_base64": base64.b64encode(b"PDF").decode(),
    }
    edit = FollowUpEdit(
        subject="Re: Quick meeting", body="Here is the booking link.",
        cc="teammate@example.com", attachments=[attachment],
    )
    assert edit.attachments[0].filename == "offer.pdf"
    with pytest.raises(ValueError):
        FollowUpEdit(subject="Re: Quick meeting", body="Here is the booking link.", cc="bad\naddress")


def test_gmail_rich_reply_keeps_thread_reference_and_attachment() -> None:
    provider = GmailOutboundProvider(uuid4(), "test-token", "team@example.com")
    sent: dict = {}

    def post(path: str, payload: dict) -> dict:
        sent.update({"path": path, **payload})
        return {"id": "sent-message", "threadId": "existing-thread"}

    provider.api.post = post
    result = provider.send_email_with_metadata(
        recipient="lead@example.com", cc="rep@example.com",
        subject="Re: Quick meeting", body="Please book here.",
        html_body="<p><b>Please book here.</b></p>",
        attachments=[("offer.txt", "text/plain", b"offer")],
        thread_id="existing-thread", in_reply_to="<source@example.com>",
    )
    assert result == {"message_id": "sent-message", "thread_id": "existing-thread"}
    assert sent["threadId"] == "existing-thread"
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(sent["raw"] + "=="))
    assert message["In-Reply-To"] == "<source@example.com>"
    assert message["References"] == "<source@example.com>"
    assert message["Cc"] == "rep@example.com"
    assert message.get_body(preferencelist=("html",)) is not None
    assert next(message.iter_attachments()).get_filename() == "offer.txt"
