import {
  Activity,
  ArrowRight,
  BarChart3,
  Building2,
  CalendarDays,
  CheckCircle2,
  Inbox,
  Mail,
  Phone,
  Route,
  ShieldCheck,
  Sparkles,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import "./CorralScopeV2.css";

type Feature = {
  number: string;
  icon: LucideIcon;
  title: string;
  summary: string;
  details: string[];
  tags: string[];
};

const flow: Array<{ title: string; detail: string; icon: LucideIcon }> = [
  { title: "Capture", detail: "Every signal", icon: Inbox },
  { title: "Understand", detail: "Explain the fit", icon: Sparkles },
  { title: "Route", detail: "Find the owner", icon: Route },
  { title: "Engage", detail: "Take the next step", icon: Workflow },
  { title: "Improve", detail: "Learn from outcomes", icon: BarChart3 },
];

const features: Feature[] = [
  {
    number: "01",
    icon: Inbox,
    title: "Capture & consolidate",
    summary: "A shared lead record, not another disconnected inbox.",
    details: [
      "Website, email, CRM and social intake",
      "Independent contact/company records, rollups and touch history",
      "Search, duplicate protection and explicit merge paths",
    ],
    tags: ["Web forms", "Email", "CRM", "Messaging"],
  },
  {
    number: "02",
    icon: Sparkles,
    title: "Explain & qualify",
    summary: "AI turns available evidence into a decision people can inspect.",
    details: [
      "ICP fit, intent, urgency and qualification score",
      "Evidence, confidence, rationale and recommended next step",
      "Manager-reviewed company enrichment and competitor insights",
      "Uncertain or restricted cases go to Manual Review",
    ],
    tags: ["Explainable AI", "ICP", "Human review"],
  },
  {
    number: "03",
    icon: Route,
    title: "Route & coordinate",
    summary: "Send work to the right team with clear operating rules.",
    details: [
      "Workspace, territory, service and industry routing",
      "Rep capacity, availability, weighted distribution and SLAs",
      "Slack notifications, queues and CRM synchronization",
    ],
    tags: ["Rules", "Capacity", "Slack", "CRM sync"],
  },
  {
    number: "04",
    icon: Mail,
    title: "Engage safely",
    summary: "Move conversations forward while keeping people in control.",
    details: [
      "Human-reviewed drafts; sequences pause on replies and never auto-send",
      "Business-hour timing and meeting coordination",
      "Outbound actions stay behind permission and consent checks",
    ],
    tags: ["Follow-up", "Meetings", "Approvals"],
  },
  {
    number: "05",
    icon: Phone,
    title: "Manage the sale",
    summary: "Keep lead qualification separate from commercial progress.",
    details: [
      "Sales stages from contact and follow-up to proposal and outcome",
      "Project or sale wins, closed/lost outcomes and member credit",
      "Admin-enabled, confirmed dialing with consent; provider analysis varies",
      "CloudTalk prospects enter Leads only after an explicit Add lead action",
    ],
    tags: ["Sales pipeline", "Contacts", "Voice"],
  },
  {
    number: "06",
    icon: ShieldCheck,
    title: "Measure & govern",
    summary: "Give teams visibility without giving automation unchecked authority.",
    details: [
      "Revenue, channel ROI, cohort and representative analytics",
      "Won/lost outcomes can suggest ICP weight drafts for admin review",
      "Opt-in research, coaching and watcher agents; workflows and approval-gated playbooks",
      "Shared notes/search, client workspaces, agency branding, consent/DNC, audit and retention",
    ],
    tags: ["Analytics", "Agents", "Collaboration", "Privacy", "Audit", "Branding"],
  },
];

const integrations = [
  {
    title: "Lead sources",
    icon: Inbox,
    providers: ["Website forms", "Gmail", "Outlook", "WhatsApp", "Instagram", "Messenger", "LinkedIn"],
  },
  {
    title: "CRM & scheduling",
    icon: Building2,
    providers: ["Salesforce", "HubSpot", "GoHighLevel", "Clutch", "Google Calendar", "Calendly"],
  },
  {
    title: "Team & calling",
    icon: Users,
    providers: ["Slack", "CloudTalk", "Twilio", "Aircall", "RingCentral", "Vapi", "Retell"],
  },
];

function IconMark({ icon: Icon, tone = "green" }: { icon: LucideIcon; tone?: "green" | "blue" | "orange" }) {
  return (
    <span className={`corral-v2-icon corral-v2-icon-${tone}`} aria-hidden="true">
      <Icon size={21} strokeWidth={1.8} />
    </span>
  );
}

export function CorralScopeV2() {
  return (
    <main className="corral-scope-v2">
      <div className="corral-v2-page">
        <header className="corral-v2-header">
          <div className="corral-v2-brand">
            <img src="/__mockup/images/corral-mark.png" alt="" />
            <span>CORRAL</span>
            <span className="corral-v2-brand-divider" />
            <span className="corral-v2-brand-subtitle">PRODUCT SCOPE</span>
          </div>
          <span className="corral-v2-version">V2 · CURRENT CAPABILITIES</span>
        </header>

        <section className="corral-v2-hero">
          <div className="corral-v2-hero-copy">
            <span className="corral-v2-eyebrow">Lead intelligence, with people in control</span>
            <h1>
              Every inquiry.
              <br />
              <span>A clear next action.</span>
            </h1>
            <p>
              Corral connects the signals, evidence and team decisions behind every lead—from
              first contact through sales outcome.
            </p>
            <div className="corral-v2-hero-note">
              <CheckCircle2 size={18} aria-hidden="true" />
              <span>Explainable decisions. Human-approved outreach. Recorded outcomes.</span>
            </div>
          </div>
          <aside className="corral-v2-loop-card">
            <span className="corral-v2-eyebrow">The operating loop</span>
            <strong>One workspace for the whole lead journey.</strong>
            <div className="corral-v2-loop-line">
              <span>Signal</span>
              <ArrowRight size={15} aria-hidden="true" />
              <span>Evidence</span>
              <ArrowRight size={15} aria-hidden="true" />
              <span>Action</span>
            </div>
            <div className="corral-v2-loop-footer">
              <Activity size={16} aria-hidden="true" />
              <span>Each step stays tied to its source and history.</span>
            </div>
          </aside>
        </section>

        <section className="corral-v2-flow" aria-label="Corral workflow">
          {flow.map((step, index) => {
            const Icon = step.icon;
            return (
              <div className="corral-v2-flow-step" key={step.title}>
                <div className="corral-v2-flow-icon">
                  <Icon size={22} strokeWidth={1.8} aria-hidden="true" />
                </div>
                <span className="corral-v2-flow-number">{String(index + 1).padStart(2, "0")}</span>
                <strong>{step.title}</strong>
                <small>{step.detail}</small>
                {index < flow.length - 1 && <ArrowRight className="corral-v2-flow-arrow" size={18} aria-hidden="true" />}
              </div>
            );
          })}
        </section>

        <section className="corral-v2-capabilities">
          <div className="corral-v2-section-heading">
            <div>
              <span className="corral-v2-eyebrow">Connected capabilities</span>
              <h2>One lead workflow. Six operating layers.</h2>
            </div>
            <p>From intake to insight, each capability works from the same workspace context.</p>
          </div>
          <div className="corral-v2-feature-grid">
            {features.map((feature, index) => (
              <article className={`corral-v2-feature corral-v2-feature-${index + 1}`} key={feature.number}>
                <div className="corral-v2-feature-top">
                  <IconMark icon={feature.icon} tone={index === 1 ? "blue" : index === 3 ? "orange" : "green"} />
                  <span className="corral-v2-feature-number">{feature.number}</span>
                </div>
                <h3>{feature.title}</h3>
                <p className="corral-v2-feature-summary">{feature.summary}</p>
                <ul>
                  {feature.details.map((detail) => (
                    <li key={detail}>
                      <CheckCircle2 size={15} aria-hidden="true" />
                      <span>{detail}</span>
                    </li>
                  ))}
                </ul>
                <div className="corral-v2-tags">
                  {feature.tags.map((tag) => <span key={tag}>{tag}</span>)}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="corral-v2-sales">
          <div className="corral-v2-sales-heading">
            <span className="corral-v2-eyebrow">Sales pipeline · alongside qualification</span>
            <h2>Follow the commercial journey—not just the lead score.</h2>
            <p>
              Recorded progress connects outreach and outcomes without replacing the
              qualification funnel.
            </p>
          </div>
          <div className="corral-v2-stage-track">
            {[
              "New lead",
              "Contacted",
              "Follow-up drafted",
              "Follow-up sent",
              "Responded",
              "Meeting scheduled",
              "Opportunity",
              "Proposal sent",
              "Negotiation",
              "Won",
              "Lost",
            ].map((stage, index) => (
              <div className={`corral-v2-stage corral-v2-stage-${index + 1}`} key={stage}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{stage}</strong>
              </div>
            ))}
          </div>
        </section>

        <section className="corral-v2-control-band">
          <div className="corral-v2-control-icon">
            <ShieldCheck size={26} strokeWidth={1.8} aria-hidden="true" />
          </div>
          <div className="corral-v2-control-copy">
            <span className="corral-v2-eyebrow">Human control is built in</span>
            <h2>AI recommends. Teams decide. Every action leaves a trail.</h2>
          </div>
          <div className="corral-v2-control-points">
            <span><CheckCircle2 size={15} aria-hidden="true" /> Outreach approval</span>
            <span><CheckCircle2 size={15} aria-hidden="true" /> Consent & DNC checks</span>
            <span><CheckCircle2 size={15} aria-hidden="true" /> Role-based access</span>
            <span><CheckCircle2 size={15} aria-hidden="true" /> Auditable history</span>
          </div>
        </section>

        <section className="corral-v2-integrations">
          <div className="corral-v2-integration-heading">
            <div>
              <span className="corral-v2-eyebrow">Connect the existing stack</span>
              <h2>Workspace-scoped integrations</h2>
            </div>
            <CalendarDays size={22} aria-hidden="true" />
          </div>
          <div className="corral-v2-integration-grid">
            {integrations.map((group) => {
              const Icon = group.icon;
              return (
                <div className="corral-v2-integration-group" key={group.title}>
                  <div className="corral-v2-integration-title">
                    <Icon size={17} aria-hidden="true" />
                    <strong>{group.title}</strong>
                  </div>
                  <div className="corral-v2-provider-list">
                    {group.providers.map((provider) => <span key={provider}>{provider}</span>)}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <footer className="corral-v2-footer">
          <div className="corral-v2-footer-brand">
            <img src="/__mockup/images/corral-mark.png" alt="" />
            <span>CORRAL</span>
          </div>
          <p>
            Implemented-scope snapshot. Live-provider certification and Zapier/Make
            marketplace publication remain pending; call details depend on the provider.
          </p>
          <span className="corral-v2-footer-label">SCOPE SUMMARY · V2</span>
        </footer>
      </div>
    </main>
  );
}
