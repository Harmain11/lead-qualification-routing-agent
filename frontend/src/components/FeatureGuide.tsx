import { useState } from "react";
import { Button, Sheet } from "../v3/ui/primitives";
import { Icon, type IconName } from "../v3/ui/Icon";

type Guide = {
  title: string;
  button: string;
  icon: IconName;
  intro: string;
  steps: Array<[string, string]>;
  tip?: string;
  stuck: string;
};

// Built-in guides. Each one describes the screen as it is today, in the order
// someone uses it, and ends with what to check when something doesn't move.
const guides = {
  lead: {
    title: "Working a lead",
    button: "How leads work",
    icon: "leads",
    intro: "A lead record is everything about one buyer: what they asked for, where the deal stands and what to do next.",
    steps: [
      ["Start at the top", "Highlights tell you what needs doing now: a reply that’s overdue, missing details, a meeting to book or a review hold. Each one has its own action."],
      ["Move the deal", "The stage rail under the header shows the journey. Choose a stage to move the deal; Corral asks for anything that stage needs, like a reason for a loss."],
      ["Read the tabs left to right", "Overview shows where things stand. Qualification explains the score. Pipeline holds the plan, meetings and outcome. Emails and Calls hold the conversation. Deal holds quotes, negotiation and service levels."],
      ["Let Corral do the busywork", "Ask Corral drafts emails, adds notes, summarises calls and updates details. Every change it proposes waits for you to approve it."],
    ],
    tip: "Pin a tab you use often from the sidebar; it opens straight to that lead and section.",
    stuck: "If a lead won’t route or score, check that a workspace ICP is published (Settings → Ideal customer profile) and that the lead has an email or company. Review holds clear from the highlight or the Review page.",
  },
  pipeline: {
    title: "The pipeline",
    button: "How the pipeline works",
    icon: "pipeline",
    intro: "The Pipeline tab is where a lead becomes revenue: the stages it has passed, what the next one needs, and how it ends.",
    steps: [
      ["Know the stages", "New → Contacted → Engaged → Quoted → Negotiation → Contract & SLA → Won. Each stage shows what it means; the current one shows how long the deal has been there."],
      ["Advance with intent", "Use the button at the top to move to the next stage, or Move here on a later one. Moves are saved with the date, so the journey stays honest."],
      ["Keep the numbers real", "Set the deal value (a sent quote sets it for you) and an expected close date. The weighted value appears once you have five closed deals to learn from."],
      ["Follow the plan, book the meeting", "The plan lists the steps most likely to reach the next stage. Meetings shows proposed times and booking links you can send."],
      ["Record the outcome", "Record win credits a teammate and the real revenue. Lost asks for a reason so your win/loss learning improves."],
    ],
    stuck: "If a stage won’t save, you may not have edit access, or the deal desk is reconnecting; wait for the saved indicator and try again.",
  },
  deals: {
    title: "Quotes, negotiation and service levels",
    button: "How deals work",
    icon: "quote",
    intro: "The Deal tab holds the commercial side: what you’re offering, what was said about price, and the terms you’ll commit to.",
    steps: [
      ["Build a quote", "Add services from your catalogue, set quantity, discount, tax and term, then save a draft. Every revision is kept as a new version."],
      ["Send it for real", "Review the quote, download a PDF or open an email draft with it attached. Marking a quote Sent records the status; sending the email delivers it."],
      ["Log the negotiation", "Record each offer and counter-offer with what was said. The price path shows the gap between your last offer and theirs."],
      ["Agree service levels", "Set response and resolution times, availability, payment terms and renewal. Marking terms Signed records a signature obtained outside Corral."],
    ],
    stuck: "If a save fails, a banner stays on screen and your draft is kept in this browser. Copy anything important before reloading.",
  },
  inbox: {
    title: "Your inbox",
    button: "How the inbox works",
    icon: "inbox",
    intro: "The inbox is your connected Gmail, organised around leads, with AI drafting that understands the whole thread.",
    steps: [
      ["Connect once", "Connect Gmail in Integrations. Corral reads conversations with your leads and sends only when you press Send."],
      ["Work the thread", "Open a conversation to read it top to bottom. Reply, Reply all and Forward work like any mail app; Cc and Bcc are under the recipients."],
      ["Write with AI", "Choose Write with AI and, optionally, tell it what you want (“shorter”, “propose Tuesday”). It reads the full thread. Undo puts your words back."],
      ["Schedule without back-and-forth", "Insert a booking link or proposed times from the scheduling menu; the lead picks a slot and the meeting lands on your calendar."],
      ["Drafts are safe", "Anything you start is saved as a draft until you send or discard it."],
    ],
    stuck: "If New email is greyed out, Gmail isn’t connected or your role can’t send. If a thread looks out of date, choose Refresh; Gmail can take a minute to sync.",
  },
  calls: {
    title: "Calls",
    button: "How calls work",
    icon: "calls",
    intro: "Every call in one place: inbound, outbound and missed, linked to the lead it belongs to.",
    steps: [
      ["Find a call", "Filter by All, Inbound, Outbound or Missed, or search a name or number."],
      ["Act on it", "Each row lets you call back, copy the number, open the lead, link the call to a lead, add the caller as a new lead, or read the transcript."],
      ["Place a call", "New call picks a lead or takes a number. You start the call yourself; Corral never dials on its own."],
      ["Keep the phone open", "The phone panel stays docked while a call is active, so you can take notes in the lead at the same time."],
    ],
    stuck: "If the phone won’t load, reconnect your calling provider in Integrations → Calling and allow microphone access when your browser asks. Calls dialled from the provider’s own keypad can take a sync before they appear.",
  },
  sequence: {
    title: "Sequences",
    button: "How sequences work",
    icon: "automations",
    intro: "A sequence is timed follow-up: wait, draft an email, wait again. Every draft waits for a person to review and send it.",
    steps: [
      ["Start from a template", "Pick Speed to lead, Post-demo follow-up or Re-engage a quiet lead, or start blank. Templates save as paused drafts."],
      ["Shape the steps", "Steps run top to bottom. Use Add step under any step. Wait sets the gap in hours, days or weeks; Draft an email takes the instructions for what that email should say."],
      ["Activate, then enroll", "Turn on Active and save, then choose Enroll leads. Activating alone doesn’t enroll anyone."],
      ["Review and send", "When a draft is due it appears in the lead’s Emails tab for review. The sequence moves on once it’s sent."],
      ["Replies pause it", "A new reply pauses the sequence so you can respond personally. Closing a lead stops it."],
    ],
    tip: "Ask Corral can draft a whole sequence for you: “build a three-step follow-up after a demo”.",
    stuck: "If nothing moves, check the sequence is Active, the lead is enrolled, the wait has passed and the person who enrolled it still has edit access. A blocked run shows its reason in Run history.",
  },
  playbook: {
    title: "Playbooks",
    button: "How playbooks work",
    icon: "route",
    intro: "A playbook turns a repeatable process into steps your team follows the same way every time, with decisions along the way.",
    steps: [
      ["Use one for a process", "Discovery checklists, hot-lead responses and handoffs are playbooks. Use a sequence when the job is timed email."],
      ["Add decisions", "Check a signal asks a question (Lead replied? Deal stalled?) and sends the lead down If yes or If no."],
      ["Choose the actions", "Note on the lead records something for the team. Coaching tip guides the rep. Research looks up the company. Draft an email prepares a message for review."],
      ["Save cleanly", "Corral checks the flow before saving and names any step that isn’t connected or loops back on itself."],
    ],
    stuck: "If a playbook won’t save, fix the steps listed in red. If a run is blocked, its reason shows in Run history: usually a missing company website, permission or AI connection.",
  },
  integrations: {
    title: "Integrations",
    button: "How integrations work",
    icon: "integrations",
    intro: "Connect the tools your team already uses. Corral only claims a connection works once it has been verified.",
    steps: [
      ["Pick a category", "Email, calendar, calling, CRM and more. The count shows how many are connected."],
      ["Connect", "Choose Connect on a card. You’ll sign in with the provider or paste the key it gives you; Corral stores secrets encrypted and never shows them again."],
      ["Verify", "Run the connection test. Connected means verified. Needs attention, Sign-in expired and Waiting for sign-in each tell you what to do next."],
      ["Choose what it may do", "Each card lists the permissions it uses. Read-only access never sends email or places calls."],
    ],
    stuck: "If a connection drops, choose Fix on its card and sign in again. If a test fails, the card says which permission or setting is missing.",
  },
  agent: {
    title: "Ask Corral",
    button: "How Ask Corral works",
    icon: "sparkle",
    intro: "Ask Corral works across your whole workspace: it finds things, opens pages and prepares changes for you to approve.",
    steps: [
      ["Pick the scope", "Workspace for questions across everything, Lead for the record you’re on."],
      ["Ask plainly", "“Open the pipeline”, “Who went quiet this week?”, “Draft a follow-up to Sara”, “Build a post-demo sequence”. Page commands happen instantly."],
      ["Approve what it proposes", "Edits, emails, notes and sequences arrive as cards. Nothing changes or sends until you approve it."],
      ["Use it as a coach", "Ask it to review a call, write meeting minutes or suggest how to handle an objection. It only uses what was actually recorded."],
    ],
    stuck: "If Ask Corral can’t answer, connect an AI provider in Integrations. If it says your role can’t do something, an admin can grant that permission in Team.",
  },
  coaching: {
    title: "Research & Coaching",
    button: "How coaching works",
    icon: "research",
    intro: "Coaching from your real conversations, research on accounts, and lessons from deals you’ve won and lost.",
    steps: [
      ["Coach a conversation", "Choose a lead and review its recorded calls and emails: questions asked, balance of talk, and what to do next."],
      ["Send a weekly digest", "Prepare a rep’s week in review. You approve it before they see it."],
      ["Learn from outcomes", "Analyse closed deals to see which ideal-customer criteria predict wins. Suggested changes become an ICP draft only if you approve them."],
      ["Handle objections", "The objection library collects real objections and how deals that heard them ended."],
    ],
    stuck: "Reports need recorded conversations or closed deals. If one is empty, the lead may have no transcripts yet.",
  },
} satisfies Record<string, Guide>;

export type GuideTopic = keyof typeof guides;

export function FeatureGuide({ topic, compact = false }: { topic: GuideTopic; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const guide: Guide = guides[topic];
  return (
    <>
      <Button kind="gray" size="sm" icon="guide" className="guide-trigger" onClick={() => setOpen(true)} aria-label={compact ? guide.button : undefined}>
        {compact ? "Guide" : guide.button}
      </Button>
      {open && (
        <Sheet title={guide.title} subtitle={guide.intro} onClose={() => setOpen(false)} width={600}>
          <div className="guide">
            <ol className="guide-steps">
              {guide.steps.map(([title, text], index) => (
                <li key={title}>
                  <span className="guide-num" aria-hidden="true">{index + 1}</span>
                  <div>
                    <h3>{title}</h3>
                    <p>{text}</p>
                  </div>
                </li>
              ))}
            </ol>
            {guide.tip && (
              <p className="guide-tip"><Icon name="sparkle" size={15} /><span>{guide.tip}</span></p>
            )}
            <details className="guide-stuck">
              <summary><Icon name="info" size={15} />If something’s stuck</summary>
              <p>{guide.stuck}</p>
            </details>
          </div>
        </Sheet>
      )}
    </>
  );
}
