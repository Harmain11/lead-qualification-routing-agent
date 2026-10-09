import { useEffect, useState } from "react";
import { createClientId } from "../clientIds";
import {
  opsGet,
  opsSave,
  type Capacity,
  type Policy,
  type PolicyResponse,
} from "../operationsApi";
const words = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
export function PoliciesView({ canManage }: { canManage: boolean }) {
  const [data, setData] = useState<PolicyResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () =>
    opsGet<PolicyResponse>("/v1/ops/policy")
      .then(setData)
      .catch((reason) => setError(reason.message));
  useEffect(() => {
    void load();
  }, []);
  function change(patch: Partial<Policy>) {
    if (data)
      setData({ ...data, configuration: { ...data.configuration, ...patch } });
    setNotice("");
  }
  async function save() {
    if (!data) return;
    setBusy(true);
    setError("");
    try {
      const saved = await opsSave<{ configuration: Policy; version: number }>(
        "/v1/ops/policy",
        { configuration: data.configuration, version: data.version },
        "PUT",
      );
      setData({ ...data, ...saved });
      setNotice("Workspace policies saved.");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to save policies",
      );
    } finally {
      setBusy(false);
    }
  }
  if (!data)
    return (
      <div className="page-view ops-page">
        {error ? (
          <p role="alert">{error}</p>
        ) : (
          <p role="status">Loading team policies…</p>
        )}
      </div>
    );
  const config = data.configuration;
  function updateCapacity(userId: string, patch: Partial<Capacity>) {
    const existing = config.capacity.find(
      (item) => item.user_id === userId,
    ) || {
      user_id: userId,
      max_open_leads: 30,
      available: true,
      available_until: null,
    };
    change({
      capacity: [
        ...config.capacity.filter((item) => item.user_id !== userId),
        { ...existing, ...patch },
      ],
    });
  }
  return (
    <div className="page-view ops-page">
      <header className="ops-page-head">
        <div>
          <span className="eyebrow">Workspace controls</span>
          <h1>Team policies</h1>
          <p>
            Define territory ownership, response targets, representative limits
            and approval boundaries.
          </p>
        </div>
      </header>
      <div className="ops-panel">
        <h2>My availability</h2>
        <p>
          Check in for the next 15 minutes. Expired check-ins stop new
          assignments until you check in again.
        </p>
        <div className="ops-toolbar">
          <button
            className="secondary-button"
            onClick={() =>
              void opsSave("/v1/ops/availability", {
                available: true,
                minutes: 15,
              })
                .then(() => load())
                .catch((reason) => setError(reason.message))
            }
          >
            Available now
          </button>
          <button
            className="button-quiet"
            onClick={() =>
              void opsSave("/v1/ops/availability", {
                available: false,
                minutes: 15,
              })
                .then(() => load())
                .catch((reason) => setError(reason.message))
            }
          >
            Pause assignments
          </button>
        </div>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <fieldset disabled={!canManage || busy} className="ops-policy-fieldset">
          <section className="ops-panel">
            <div className="ops-toolbar">
              <div>
                <h2>Groups & territories</h2>
                <p>
                  Policies match in listed order. A configured territory with no
                  matching group keeps the lead in its queue.
                </p>
              </div>
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  change({
                    territories: [
                      ...config.territories,
                      {
                        id: createClientId(),
                        name: "New territory",
                        member_ids: [],
                        regions: [],
                        industries: [],
                        products: [],
                        sla_hours: 24,
                      },
                    ],
                  })
                }
              >
                Add group
              </button>
            </div>
            {config.territories.map((group, index) => (
              <article className="ops-group" key={group.id}>
                <div className="ops-form-grid">
                  <label>
                    Group name
                    <input
                      required
                      value={group.name}
                      maxLength={100}
                      onChange={(event) =>
                        change({
                          territories: config.territories.map((value) =>
                            value.id === group.id
                              ? { ...value, name: event.target.value }
                              : value,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Regions, comma-separated
                    <input
                      defaultValue={group.regions.join(", ")}
                      onBlur={(event) =>
                        change({
                          territories: config.territories.map((value) =>
                            value.id === group.id
                              ? { ...value, regions: words(event.target.value) }
                              : value,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Industries
                    <input
                      defaultValue={group.industries.join(", ")}
                      onBlur={(event) =>
                        change({
                          territories: config.territories.map((value) =>
                            value.id === group.id
                              ? {
                                  ...value,
                                  industries: words(event.target.value),
                                }
                              : value,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Products/services
                    <input
                      defaultValue={group.products.join(", ")}
                      onBlur={(event) =>
                        change({
                          territories: config.territories.map((value) =>
                            value.id === group.id
                              ? {
                                  ...value,
                                  products: words(event.target.value),
                                }
                              : value,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Response target, hours
                    <input
                      type="number"
                      required
                      min={1}
                      max={720}
                      value={group.sla_hours}
                      onChange={(event) =>
                        change({
                          territories: config.territories.map((value) =>
                            value.id === group.id
                              ? {
                                  ...value,
                                  sla_hours: Number(event.target.value),
                                }
                              : value,
                          ),
                        })
                      }
                    />
                  </label>
                </div>
                <fieldset>
                  <legend>Selected members</legend>
                  <div className="ops-check-grid">
                    {data.members.map((member) => (
                      <label className="ops-check" key={member.user_id}>
                        <input
                          type="checkbox"
                          checked={group.member_ids.includes(member.user_id)}
                          onChange={(event) =>
                            change({
                              territories: config.territories.map((value) =>
                                value.id === group.id
                                  ? {
                                      ...value,
                                      member_ids: event.target.checked
                                        ? [...value.member_ids, member.user_id]
                                        : value.member_ids.filter(
                                            (id) => id !== member.user_id,
                                          ),
                                    }
                                  : value,
                              ),
                            })
                          }
                        />
                        {member.name}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="ops-toolbar">
                  <small>Priority {index + 1}</small>
                  <button
                    type="button"
                    className="button-quiet"
                    disabled={index === 0}
                    onClick={() => {
                      const groups = [...config.territories];
                      [groups[index - 1], groups[index]] = [
                        groups[index],
                        groups[index - 1],
                      ];
                      change({ territories: groups });
                    }}
                  >
                    Move earlier
                  </button>
                  <button
                    type="button"
                    className="button-quiet"
                    onClick={() =>
                      change({
                        territories: config.territories.filter(
                          (value) => value.id !== group.id,
                        ),
                      })
                    }
                  >
                    Remove group
                  </button>
                </div>
              </article>
            ))}
            {!config.territories.length && (
              <p className="ops-muted">
                No territory restriction is configured. Existing weighted
                workload routing applies.
              </p>
            )}
          </section>
          <section className="ops-panel">
            <h2>Representative capacity</h2>
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Open leads</th>
                    <th>Maximum</th>
                    <th>Available</th>
                    <th>Check-in expiry</th>
                  </tr>
                </thead>
                <tbody>
                  {data.members.map((member) => {
                    const state = config.capacity.find(
                      (value) => value.user_id === member.user_id,
                    );
                    return (
                      <tr key={member.user_id}>
                        <td>{member.name}</td>
                        <td>{member.open_leads}</td>
                        <td>
                          <input
                            aria-label={member.name + " maximum open leads"}
                            type="number"
                            min={0}
                            max={10000}
                            value={state?.max_open_leads ?? 30}
                            onChange={(event) =>
                              updateCapacity(member.user_id, {
                                max_open_leads: Number(event.target.value),
                              })
                            }
                          />
                        </td>
                        <td>
                          <input
                            aria-label={member.name + " available"}
                            type="checkbox"
                            checked={state?.available ?? true}
                            onChange={(event) =>
                              updateCapacity(member.user_id, {
                                available: event.target.checked,
                                available_until: null,
                              })
                            }
                          />
                        </td>
                        <td>
                          {state?.available_until
                            ? new Date(state.available_until).toLocaleString()
                            : "Manual setting"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
          <section className="ops-panel">
            <h2>Agent approvals & monitoring</h2>
            <div className="ops-form-grid">
              {["research", "coach", "note"].map((action) => (
                <label key={action}>
                  {action} approval
                  <select
                    value={config.approval_tiers[action] || "approve"}
                    onChange={(event) =>
                      change({
                        approval_tiers: {
                          ...config.approval_tiers,
                          [action]: event.target.value,
                        },
                      })
                    }
                  >
                    <option value="automatic">Automatic internal work</option>
                    <option value="approve">One-click approval</option>
                    <option value="full_review">
                      Review by another manager
                    </option>
                  </select>
                </label>
              ))}
            </div>
            <p>
              Follow-up drafts always require a representative to review and
              send. Trust scores never grant additional access.
            </p>
            <label className="ops-check">
              <input
                type="checkbox"
                checked={config.watcher_enabled}
                onChange={(event) =>
                  change({ watcher_enabled: event.target.checked })
                }
              />
              Watch acquired email and progress evidence continuously
            </label>
            <label className="ops-check">
              <input
                type="checkbox"
                checked={config.researcher_enabled}
                onChange={(event) =>
                  change({ researcher_enabled: event.target.checked })
                }
              />
              Research reviewed company pages, at most once a day per company
            </label>
            <label className="ops-check">
              <input
                type="checkbox"
                checked={config.coach_digest_enabled}
                onChange={(event) =>
                  change({ coach_digest_enabled: event.target.checked })
                }
              />
              Prepare weekly coaching digests for manager review
            </label>
            <p className="ops-muted">
              Company research uses your connected OpenAI account. Results wait
              for review before profile enrichment.
            </p>
          </section>
          <section className="ops-panel">
            <div className="ops-toolbar">
              <h2>Competitor battlecards</h2>
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  change({
                    battlecards: [
                      ...config.battlecards,
                      { competitor: "", response: "", evidence_url: null },
                    ],
                  })
                }
              >
                Add battlecard
              </button>
            </div>
            {config.battlecards.map((card, index) => (
              <div className="ops-form-grid ops-note" key={index}>
                <label>
                  Competitor
                  <input
                    required
                    minLength={2}
                    value={card.competitor}
                    onChange={(event) =>
                      change({
                        battlecards: config.battlecards.map((value, i) =>
                          i === index
                            ? { ...value, competitor: event.target.value }
                            : value,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Evidence link
                  <input
                    type="url"
                    value={card.evidence_url || ""}
                    onChange={(event) =>
                      change({
                        battlecards: config.battlecards.map((value, i) =>
                          i === index
                            ? {
                                ...value,
                                evidence_url: event.target.value || null,
                              }
                            : value,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Suggested response
                  <textarea
                    required
                    value={card.response}
                    onChange={(event) =>
                      change({
                        battlecards: config.battlecards.map((value, i) =>
                          i === index
                            ? { ...value, response: event.target.value }
                            : value,
                        ),
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  className="button-quiet"
                  onClick={() =>
                    change({
                      battlecards: config.battlecards.filter(
                        (_, i) => i !== index,
                      ),
                    })
                  }
                >
                  Remove
                </button>
              </div>
            ))}
          </section>
          <section className="ops-panel">
            <h2>Channel costs & forecast target</h2>
            <label>
              Monthly quota, USD
              <input
                type="number"
                min={0}
                step="0.01"
                value={config.monthly_quota_usd ?? ""}
                onChange={(event) =>
                  change({
                    monthly_quota_usd: event.target.value
                      ? Number(event.target.value)
                      : null,
                  })
                }
              />
            </label>
            <p>
              Attribute costs to an intake source and month. ROI is withheld
              when cost is unknown or zero.
            </p>
            {config.channel_costs.map((cost, index) => (
              <div className="ops-form-grid" key={index}>
                <label>
                  Source
                  <input
                    required
                    value={cost.source}
                    onChange={(event) =>
                      change({
                        channel_costs: config.channel_costs.map((value, i) =>
                          i === index
                            ? { ...value, source: event.target.value }
                            : value,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Intake month
                  <input
                    type="month"
                    required
                    value={cost.month}
                    onChange={(event) =>
                      change({
                        channel_costs: config.channel_costs.map((value, i) =>
                          i === index
                            ? { ...value, month: event.target.value }
                            : value,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Cost, USD
                  <input
                    type="number"
                    required
                    min={0}
                    step="0.01"
                    value={cost.cost_usd}
                    onChange={(event) =>
                      change({
                        channel_costs: config.channel_costs.map((value, i) =>
                          i === index
                            ? { ...value, cost_usd: Number(event.target.value) }
                            : value,
                        ),
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  className="button-quiet"
                  onClick={() =>
                    change({
                      channel_costs: config.channel_costs.filter(
                        (_, i) => i !== index,
                      ),
                    })
                  }
                >
                  Remove cost
                </button>
              </div>
            ))}
            <button
              type="button"
              className="secondary-button"
              onClick={() =>
                change({
                  channel_costs: [
                    ...config.channel_costs,
                    {
                      source: "web",
                      month: new Date().toISOString().slice(0, 7),
                      cost_usd: 0,
                    },
                  ],
                })
              }
            >
              Add source cost
            </button>
          </section>
        </fieldset>
        {error && (
          <p role="alert" className="team-alert">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="ops-success">
            {notice}
          </p>
        )}
        {canManage && (
          <button className="primary-button" disabled={busy}>
            {busy ? "Saving…" : "Save workspace policies"}
          </button>
        )}
        {!canManage && (
          <p className="ops-muted">
            An owner or administrator can change these policies.
          </p>
        )}
      </form>
    </div>
  );
}
