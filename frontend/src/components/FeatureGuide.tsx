import { useState } from "react";
import { Button, Sheet } from "../v3/ui/primitives";

const guides = {
  sequence: {
    title: "Sequences guide",
    purpose: "Prepare consistent follow-ups at the right time and keep every lead’s next step visible.",
    steps: [
      ["Create and activate", "Choose New sequence, name it, set the contact timezone and preferred hour, and enable Active for enrolled leads. Start → Wait → Draft → End is a ready-to-use first sequence."],
      ["Set the cadence", "Select Wait and enter hours, days or weeks. The draft is prepared after that interval at the next preferred contact window. Add another Wait and Draft pair for another follow-up."],
      ["Enroll selected leads", "Save the definition, then choose Enroll leads. Select open leads that should follow this cadence. Enrollment does not happen automatically when a definition is activated."],
      ["Run and review", "Corral checks scheduled work every 30 seconds when its operations worker is running. Check due step runs a due step immediately. When a draft is ready, open the lead’s Email section, review it and send. The sequence proceeds after the sent receipt is recorded."],
      ["Handle replies and pauses", "New replies pause a sequence. Review the conversation before choosing Review & resume. Closing a lead stops its sequence. Pausing a definition prevents its next actions until reactivated."],
    ],
    recovery: "If nothing moves: check that the definition is Active, the lead is enrolled, the due time has arrived, and the enrolling member still has lead-edit access. Research and coaching also need agent-execution access. A blocked run shows its reason; fix it and choose Review & resume. Existing runs retain the saved steps they enrolled with.",
  },
  playbook: {
    title: "Playbooks guide",
    purpose: "Turn a repeatable sales process into a recorded set of decisions, notes, research and coaching steps.",
    steps: [
      ["Begin with a process", "Use a playbook for a discovery checklist, a hot-lead response or a manager review. Use a sequence when the main requirement is timed follow-up email."],
      ["Connect the steps", "Every playbook has one Start and at least one End. Select a step to change its action and next connection. Add step inserts a connected note after the selected step. Drag steps to arrange them; arrow keys also move a selected step."],
      ["Add decisions", "A Condition checks a recorded signal such as hot, replied, stalled, won or lost. Set both If yes and If no. Every branch must reach an End; loops and disconnected steps cannot be saved."],
      ["Choose useful actions", "Note adds a shared lead note. Coach evaluates recorded evidence. Research uses a linked company and a reviewed website. Draft prepares a follow-up for a person to review. Wait schedules the next action."],
      ["Activate, enroll, and approve", "Save an active definition and enroll leads. Steps follow the workspace’s approval policy. Approve step releases the current action. Full review needs a different member with review-management access. Trust scores show observed results and never grant permissions."],
    ],
    recovery: "If saving fails, connect every step and fill both branches on each Condition. If execution is blocked, open the run’s error: review membership, permissions, company website and AI connection. Run history retains the enrolled specification even when you edit the definition later.",
  },
  deals: {
    title: "Pipeline, contracts & revenue guide",
    purpose: "Keep deal value, quotation versions, negotiation and service commitments attached to the lead and shared across the workspace.",
    steps: [
      ["Track the stage", "Move leads through New, Contacted, Engaged, Quoted, Negotiation and Contract & SLA. Stage changes are saved with history. Closed leads use the existing Record a win or Record as lost workflow."],
      ["Build a quotation", "Add service lines, quantities, discounts, tax and a monthly term. Save a draft, preview or download it, and open an email draft when ready. Marking a quotation Sent records its status; send the composed email to deliver it."],
      ["Record negotiations and terms", "Capture client offers, counter-offers and agreements. Set response time, resolution time, availability, support window, payment terms and renewal. Recording Signed requires the signer’s name and date; it records a signature obtained elsewhere."],
      ["Record actual revenue", "A pipeline amount is a forecast. Record a win with the actual revenue and credited member to update closing and revenue reporting. Revenue goals and service catalog settings are managed by workspace administrators."],
    ],
    recovery: "If a save fails, Corral shows a persistent banner and retains the draft in the current browser. Reload saved records only after copying any unsaved edits you need. A conflicting update must be reconciled before another save.",
  },
  agent: {
    title: "Ask Corral guide",
    purpose: "Find evidence, navigate the workspace and prepare reviewed actions from one place.",
    steps: [
      ["Choose the scope", "Workspace answers broad questions and finds leads or modules. Lead works with the selected record. Choose Lead, search a name, company or email, and select the result to open its context."],
      ["Ask for a concrete action", "Try ‘Find Alex at Acme’, ‘Open the inbox’, ‘Add a note that discovery is booked’, or ‘Update this lead’s job title’. Check the proposed record and values before applying a change."],
      ["Review communication", "Email proposals stay editable before sending. Calling opens the phone or a reviewed call action with the selected lead. Live email and voice actions need a verified provider and your workspace permissions."],
    ],
    recovery: "The sidebar, global search and Lead picker work without a paid AI connection. Agent conversations need a verified workspace AI provider. An unavailable provider or missing permission must be resolved before the related action can run.",
  },
  integrations: {
    title: "Connections & embedded phones guide",
    purpose: "Connect the accounts your team uses and keep sender, dialer and sync choices explicit.",
    steps: [
      ["Choose a provider", "Use Integrations in the sidebar, then its category and provider submenu. Open the provider card for its setup guide, permissions, connection test and sync settings."],
      ["Connect and verify", "Use the provider’s sign-in or credential form. Choose the correct workspace account. Test the connection and review any required permissions. Read-only access does not enable email sending or calling."],
      ["Use the embedded phone", "Open Phone and sign in to the connected provider. Corral accepts call-state messages only from the trusted provider frame. Keep the phone open while a call is active. Browser microphone access is requested by the provider."],
      ["Understand call history", "Corral-initiated calls use the reviewed call flow. A call dialed inside a provider’s own keypad may need that provider’s webhook or sync before it can be linked to a Corral lead. Its own keypad controls remain owned by the provider."],
    ],
    recovery: "If a phone fails to load, check the configured provider, public embed client ID where required, provider login and browser permissions. Reconnect expired accounts in Integrations. External provider login and actual call delivery require a configured live account.",
  },
};

export function FeatureGuide({ topic }: { topic: keyof typeof guides }) {
  const [open, setOpen] = useState(false);
  const guide = guides[topic];
  return <>
    <Button icon="guide" onClick={() => setOpen(true)}>{topic === "sequence" ? "Sequences guide" : topic === "playbook" ? "Playbooks guide" : "Guide"}</Button>
    {open && <Sheet title={guide.title} onClose={() => setOpen(false)} width={640}>
      <div className="corral-guide"><p>{guide.purpose}</p>
        {guide.steps.map(([title, text]) => <section key={title}><h3>{title}</h3><p>{text}</p></section>)}
        <section><h3>When something needs attention</h3><p>{guide.recovery}</p></section>
      </div>
    </Sheet>}
  </>;
}
