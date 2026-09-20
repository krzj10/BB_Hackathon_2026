import * as React from "react";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { ActionApproval } from "../components/approvals/ActionApproval";
import { getEvaClient } from "../api/client";
import type {
  Decision,
  ProposedAction,
  ProposedActionResponse,
  ActionResponse,
  ActionConfirmResponse,
  ApprovalRequest,
  DecisionOutcomeProposalRequest,
} from "../api/types.generated";
import { useEvaQuery } from "../hooks/useEvaQuery";
import { formatDeadline, formatMoney, formatTimeInWarsaw } from "../lib/format";

function formatRisk(risk: Decision["risk"]): { label: string; className: string } {
  switch (risk) {
    case "high":
      return { label: "High risk", className: "bg-danger/10 text-danger border-danger/30" };
    case "medium":
      return { label: "Medium risk", className: "bg-primary/10 text-primary border-primary/30" };
    default:
      return { label: "Low risk", className: "bg-muted text-muted-foreground border-border/60" };
  }
}

function formatStatus(status: Decision["status"]): { label: string; className: string } {
  switch (status) {
    case "needs_review":
      return { label: "Needs review", className: "bg-primary/10 text-primary border-primary/30" };
    case "deferred":
      return { label: "Deferred", className: "bg-warning/10 text-warning border-warning/30" };
    case "resolved":
      return { label: "Resolved", className: "bg-success/10 text-success border-success/30" };
    case "dismissed":
      return { label: "Dismissed", className: "bg-muted text-muted-foreground border-border/60" };
    default:
      return { label: status, className: "bg-muted text-muted-foreground border-border/60" };
  }
}

/** Truthful canonical ProposedAction status labels — never "executed" for merely approved. */
function formatActionStatus(status: ProposedAction["status"]): { label: string; className: string } {
  switch (status) {
    case "pending":
      return { label: "Pending", className: "bg-primary/10 text-primary border-primary/30" };
    case "approved":
      return { label: "Approved", className: "bg-success/10 text-success border-success/30" };
    case "executing":
      return { label: "Executing…", className: "bg-warning/10 text-warning border-warning/30" };
    case "succeeded":
      return { label: "Succeeded", className: "bg-success/10 text-success border-success/30" };
    case "failed":
      return { label: "Failed", className: "bg-danger/10 text-danger border-danger/30" };
    case "unknown":
      return { label: "Unknown", className: "bg-muted text-muted-foreground border-border/60" };
    case "rejected":
      return { label: "Rejected", className: "bg-danger/10 text-danger border-danger/30" };
    case "expired":
      return { label: "Expired", className: "bg-muted text-muted-foreground border-border/60" };
    case "superseded":
      return { label: "Superseded", className: "bg-muted text-muted-foreground border-border/60" };
    default:
      return { label: status, className: "bg-muted text-muted-foreground border-border/60" };
  }
}

type DecisionDetailState =
  | { phase: "detail"; decision: Decision }
  | { phase: "proposing"; decision: Decision; outcome: "accept" | "reject" }
  | { phase: "approval"; decision: Decision; proposedAction: ProposedAction }
  | { phase: "proposal-registered"; decision: Decision; proposedAction: ProposedAction }
  | { phase: "confirming"; decision: Decision; proposedAction: ProposedAction; choice: "approve" | "reject" }
  | { phase: "result"; decision: Decision; confirmResponse: ActionConfirmResponse };

interface DecisionDetailContainerProps {
  decision: Decision;
  onClose: () => void;
  setReloadKey: React.Dispatch<React.SetStateAction<number>>;
}

function DecisionDetailContainer({ decision, onClose, setReloadKey }: DecisionDetailContainerProps) {
  const client = getEvaClient();
  const [state, setState] = React.useState<DecisionDetailState>({ phase: "detail", decision });
  const [checkedAction, setCheckedAction] = React.useState<ProposedAction | null>(null);

  const handleRecordOutcome = async (outcome: "accept" | "reject") => {
    setState({ phase: "proposing", decision, outcome });
    try {
      const request: DecisionOutcomeProposalRequest = {
        outcome,
        session_id: "sess-demo-001",
        request_id: `req-${Date.now()}`,
      };
      const response: ProposedActionResponse = await client.proposeDecisionOutcome(decision.id, request);
      const proposedAction = response.action;

      // The backend field is authoritative — never inferred from risk.
      // The challenge is NOT fetched here; only after an explicit UI choice.
      if (proposedAction.requires_approval) {
        setState({ phase: "approval", decision, proposedAction });
      } else {
        setState({ phase: "proposal-registered", decision, proposedAction });
      }
    } catch (error) {
      console.error("Failed to propose decision outcome:", error);
      setState({ phase: "detail", decision });
    }
  };

  const handleDefer = async () => {
    try {
      await client.deferDecision(decision.id);
      setReloadKey((n) => n + 1);
      onClose();
    } catch (error) {
      console.error("Failed to defer decision:", error);
    }
  };

  const handleConfirmChoice = async (choice: "approve" | "reject") => {
    if (state.phase !== "approval") return;
    const { proposedAction, decision: currentDecision } = state;
    setState({ phase: "confirming", decision: currentDecision, proposedAction, choice });

    try {
      // Challenge is obtained only NOW, after the explicit UI choice, through
      // the client flow — never manufactured or reused by the UI.
      const challengeResponse = await client.getApprovalChallenge(proposedAction.id);
      if (
        challengeResponse.action_id !== proposedAction.id ||
        challengeResponse.revision !== proposedAction.revision ||
        challengeResponse.arguments_digest !== proposedAction.arguments_digest
      ) {
        throw new Error("Approval challenge binding does not match the proposed action");
      }

      const confirmRequest: ApprovalRequest = {
        action_id: proposedAction.id,
        revision: proposedAction.revision,
        arguments_digest: proposedAction.arguments_digest,
        choice,
        challenge: challengeResponse.challenge,
      };

      const confirmResponse: ActionConfirmResponse = await client.confirmAction(proposedAction.id, confirmRequest);
      setState({ phase: "result", decision: currentDecision, confirmResponse });
    } catch (error) {
      console.error("Failed to confirm action:", error);
      setState({ phase: "detail", decision: currentDecision });
    }
  };

  const handleCheckActionState = async (actionId: string) => {
    try {
      const response: ActionResponse = await client.getAction(actionId);
      setCheckedAction(response.action);
    } catch (error) {
      console.error("Failed to read action state:", error);
    }
  };

  const riskStyle = formatRisk(decision.risk);
  const statusStyle = formatStatus(decision.status);

  const renderDetail = () => (
    <div className="mx-auto w-full max-w-3xl px-6 py-8" role="dialog" aria-labelledby="decision-detail-title" aria-modal="true">
      <button
        type="button"
        className="absolute right-6 top-6 rounded-full p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={onClose}
        aria-label="Close detail"
      >
        ✕
      </button>

      <header className="mb-6">
        <div className="flex items-center gap-2 mb-2">
          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${riskStyle.className}`}>
            {riskStyle.label}
          </span>
          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${statusStyle.className}`}>
            {statusStyle.label}
          </span>
        </div>
        <h1 id="decision-detail-title" className="text-[22px] font-semibold leading-snug">{decision.title}</h1>
        {decision.money && (
          <p className="mt-2 text-[18px] font-mono tabular-nums text-foreground">{formatMoney(decision.money)}</p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[12.5px] text-muted-foreground">
          {decision.deadline && (
            <span className="text-amber-600 dark:text-amber-400">Deadline {formatDeadline(decision.deadline)}</span>
          )}
          {decision.suggested_next_step && (
            <span>Suggested: {decision.suggested_next_step}</span>
          )}
        </div>
      </header>

      <div className="space-y-6">
        {decision.context && decision.context.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">📋</span>
                Context
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1 pl-4 list-disc text-[13px]">
                {decision.context.map((c, i) => (
                  <li key={i}>{c.text}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {decision.alternatives && decision.alternatives.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">🔀</span>
                Alternatives
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1 pl-4 list-disc text-[13px]">
                {decision.alternatives.map((a, i) => (
                  <li key={i}>{a.text}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {decision.risks && decision.risks.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">⚠️</span>
                Risks
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1 pl-4 list-disc text-[13px] text-red-600 dark:text-red-400">
                {decision.risks.map((r, i) => (
                  <li key={i}>{r.text}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {decision.preference_conflicts && decision.preference_conflicts.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">⚔️</span>
                Preference Conflicts
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1 pl-4 list-disc text-[13px] text-amber-600 dark:text-amber-400">
                {decision.preference_conflicts.map((c, i) => (
                  <li key={i}>{c.text}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {decision.sources && decision.sources.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">📎</span>
                Sources & Evidence
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {decision.sources.map((src) => (
                  <li key={src.id} className="flex items-start gap-3 p-2 rounded-lg border border-border/60 hover:bg-accent transition-colors">
                    <span className="mt-0.5 text-muted-foreground">📄</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium">{src.title}</p>
                      <p className="mt-0.5 text-[12px] text-muted-foreground">
                        {src.kind.replace("_", " ")} · {formatTimeInWarsaw(src.retrieved_at)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {state.phase === "detail" && decision.status === "needs_review" && (
          <Card className="border-primary/30">
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">📝</span>
                Record Outcome
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-[13px] text-muted-foreground">
                <strong>Important:</strong> Recording ACCEPT or REJECT is an <strong>internal/local decision record</strong> only.
                It does <strong>not</strong> imply EVA sends payment, purchases anything, signs a contract, commits to a supplier,
                or sends any external instruction.
              </p>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => handleRecordOutcome("accept")}
                  className="rounded-control bg-success px-4 py-2 text-[13.5px] font-medium text-white transition-colors hover:bg-green-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-success"
                >
                  RECORD ACCEPT
                </button>
                <button
                  type="button"
                  onClick={() => handleRecordOutcome("reject")}
                  className="rounded-control bg-danger px-4 py-2 text-[13.5px] font-medium text-white transition-colors hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger"
                >
                  RECORD REJECT
                </button>
                <button
                  type="button"
                  onClick={handleDefer}
                  className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  DEFER
                </button>
              </div>
              <p className="text-[12px] text-subtle-foreground">
                DEFER is not equivalent to reject/accept. It postpones the decision.
              </p>
            </CardContent>
          </Card>
        )}

        {state.phase === "detail" && (
          <button
            type="button"
            disabled
            className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-muted-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            title="Preview — assistant integration not connected"
          >
            ASK EVA
          </button>
        )}

        {state.phase === "proposing" && (
          <Card className="border-primary/30 bg-primary/5 dark:bg-primary/10">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-primary motion-safe:animate-pulse">⏳</span>
                Proposing outcome…
              </CardTitle>
            </CardHeader>
              <CardContent>
                <p className="text-[13px] text-muted-foreground">Creating outcome proposal…</p>
              </CardContent>
          </Card>
        )}

        {state.phase === "approval" && (
          <ActionApproval
            action={state.proposedAction}
            onConfirm={handleConfirmChoice}
            onClose={onClose}
          />
        )}

        {state.phase === "confirming" && (
          <Card className="border-primary/30 bg-primary/5 dark:bg-primary/10">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-primary motion-safe:animate-pulse">⏳</span>
                {state.choice === "approve" ? "Confirming approval…" : "Confirming rejection…"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-muted-foreground">
                Obtaining the one-time challenge and submitting the confirmation…
              </p>
            </CardContent>
          </Card>
        )}

        {state.phase === "result" && (() => {
          const actionStatus = formatActionStatus(state.confirmResponse.action.status);
          const isSuccess = state.confirmResponse.action.status === "succeeded";
          const isApproved = state.confirmResponse.action.status === "approved";
          const isFailed = state.confirmResponse.action.status === "failed" || state.confirmResponse.action.status === "rejected";
          const cardClass = isSuccess || isApproved
            ? "border-success/30 bg-success/5 dark:bg-success/10"
            : isFailed
              ? "border-danger/30 bg-danger/5 dark:bg-danger/10"
              : "border-muted bg-muted/30";
          return (
            <Card className={cardClass}>
              <CardHeader>
                <CardTitle className="text-[13px] flex items-center gap-2">
                  <span>{isSuccess ? "✅" : isFailed || state.confirmResponse.action.status === "rejected" ? "🚫" : "ℹ️"}</span>
                  {actionStatus.label}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className={`text-[13px] font-medium ${isSuccess ? "text-success" : isFailed || state.confirmResponse.action.status === "rejected" ? "text-danger" : "text-muted-foreground"}`}>
                  Action status: {actionStatus.label}.
                </p>
                {state.confirmResponse.receipt && (
                  <p className="text-[12px] text-muted-foreground">
                    Approval receipt: {state.confirmResponse.receipt.id} via {state.confirmResponse.receipt.channel} at {formatTimeInWarsaw(state.confirmResponse.receipt.approved_at)}
                  </p>
                )}
                {state.confirmResponse.result && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-[12.5px] text-primary">Tool result details</summary>
                    <pre className="mt-2 text-[11px] text-muted-foreground bg-muted p-2 rounded border border-border/60 overflow-auto max-h-32">
                      {JSON.stringify(state.confirmResponse.result, null, 2)}
                    </pre>
                  </details>
                )}
                <button
                  type="button"
                  onClick={onClose}
                  className="mt-3 rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent"
                >
                  Close
                </button>
              </CardContent>
            </Card>
          );
        })()}

        {state.phase === "proposal-registered" && (() => {
          const actionStatus = formatActionStatus(state.proposedAction.status);
          return (
            <Card className="border-primary/30">
              <CardHeader>
                <CardTitle className="text-[13px] flex items-center gap-2">
                  <span className="text-muted-foreground">📝</span>
                  Proposal registered — no approval required
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-[13px] leading-relaxed">{state.proposedAction.summary}</p>
                <p className="text-[13px] text-muted-foreground">
                  This proposal does not require explicit approval. Current canonical status:{" "}
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${actionStatus.className}`}>
                    {actionStatus.label}
                  </span>
                </p>
                {checkedAction && checkedAction.id === state.proposedAction.id && (
                  <p className="text-[12.5px] text-muted-foreground">
                    Latest canonical state:{" "}
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${formatActionStatus(checkedAction.status).className}`}>
                      {formatActionStatus(checkedAction.status).label}
                    </span>
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => handleCheckActionState(state.proposedAction.id)}
                  className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Check action state
                </button>
              </CardContent>
            </Card>
          );
        })()}

        {decision.status === "resolved" && decision.outcome && state.phase === "detail" && (
          <Card className="border-success/30 bg-success/5 dark:bg-success/10">
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">✅</span>
                Outcome Recorded
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              <p className="text-[13px] font-medium text-success">
                Outcome: {decision.outcome.toUpperCase()}
              </p>
              {decision.outcome_recorded_at && (
                <p className="text-[12.5px] text-muted-foreground">
                  Recorded {formatTimeInWarsaw(decision.outcome_recorded_at)}
                </p>
              )}
              {decision.proposed_action_id && (
                <p className="text-[12.5px] text-muted-foreground">
                  Proposed action: {decision.proposed_action_id}
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {decision.status === "deferred" && state.phase === "detail" && (
          <Card className="border-warning/30 bg-warning/5 dark:bg-warning/10">
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">⏸️</span>
                Deferred
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-warning">
                This decision has been deferred. It will remain in your queue until reviewed.
              </p>
            </CardContent>
          </Card>
        )}

        {decision.status === "dismissed" && state.phase === "detail" && (
          <Card className="border-muted bg-muted/30">
            <CardHeader>
              <CardTitle className="text-[14px] flex items-center gap-2">
                <span className="text-muted-foreground">🗑️</span>
                Dismissed
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-muted-foreground">
                This decision was dismissed and no outcome was recorded.
              </p>
            </CardContent>
          </Card>
        )}

        <p className="text-center text-[12px] text-subtle-foreground">
          Data from canonical fixtures: decision_finance_pln_needs_review.json / decision_finance_pln_resolved.json
        </p>
      </div>
    </div>
  );

  return <>{renderDetail()}</>;
}

function DecisionRow({ decision, onClick }: { decision: Decision; onClick: () => void }) {
  const riskStyle = formatRisk(decision.risk);
  const statusStyle = formatStatus(decision.status);

  return (
    <li className="group" onClick={onClick}>
      <button
        type="button"
        className="w-full text-left flex gap-3 py-3 pr-4 hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-label={`Open ${decision.title}`}
      >
        <span className={`mt-0.5 grid size-6 shrink-0 place-items-center rounded-md ${riskStyle.className}`}>
          💰
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-[13.5px] font-medium leading-snug group-hover:underline">{decision.title}</p>
            <Badge variant="outline" className={riskStyle.className}>
              {riskStyle.label}
            </Badge>
          </div>
          <p className="mt-0.5 text-[12px] text-muted-foreground flex flex-wrap gap-3">
            {decision.money && <span>{formatMoney(decision.money)}</span>}
            {decision.deadline && <span>Deadline {formatDeadline(decision.deadline)}</span>}
            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${statusStyle.className}`}>
              {statusStyle.label}
            </span>
          </p>
        </div>
        <span className="shrink-0 text-muted-foreground">→</span>
      </button>
      <div className="h-px bg-border/60" />
    </li>
  );
}

function DecisionError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mx-auto w-full max-w-md px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">Decisions unavailable</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        The decision inbox could not be loaded. Nothing was faked in its place — retry when
        the connection to the EVA backend is available.
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-6 rounded-control bg-primary px-4 py-2 text-[13.5px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        Retry
      </button>
    </div>
  );
}

function DecisionLoading() {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8" aria-busy="true" aria-live="polite">
      <div className="h-4 w-48 motion-safe:animate-pulse rounded-full bg-muted" />
      <div className="mt-6 space-y-3">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-14 motion-safe:animate-pulse rounded-card bg-muted" />
        ))}
      </div>
      <p className="mt-6 text-[12.5px] text-muted-foreground">Loading decisions…</p>
    </div>
  );
}

function DecisionEmpty() {
  return (
    <div className="mx-auto w-full max-w-md px-6 py-24 text-center">
      <h2 className="text-[17px] font-semibold tracking-tight">No decisions to record</h2>
      <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
        Your decision inbox is empty. Press the orb and just ask whenever you need something.
      </p>
      <Badge variant="outline" className="mt-5">
        Empty state
      </Badge>
    </div>
  );
}

export default function Decisions() {
  const [reloadKey, setReloadKey] = React.useState(0);
  const [selectedDecision, setSelectedDecision] = React.useState<Decision | null>(null);
  const client = getEvaClient();
  const decisions = useEvaQuery(`decisions:${reloadKey}`, (c) => c.getDecisions(), client);
  const retry = () => setReloadKey((n) => n + 1);

  if (decisions.status === "loading") return <DecisionLoading />;
  if (decisions.status === "error") return <DecisionError onRetry={retry} />;
  if (decisions.status === "ready" && decisions.data.items.length === 0) return <DecisionEmpty />;

  const items = decisions.data.items;

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8 lg:px-8 lg:py-10">
      <header className="mb-6">
        <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.02em]">Decisions</h1>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">
          Decisions surfaced from attention, ready to record outcomes. Tap a decision for details and controls.
        </p>
      </header>

      <ul className="divide-y divide-border/60 border-y border-border/60 rounded-card overflow-hidden">
        {items.map((decision) => (
          <DecisionRow key={decision.id} decision={decision} onClick={() => setSelectedDecision(decision)} />
        ))}
      </ul>

      {selectedDecision && (
        <DecisionDetailContainer
          decision={selectedDecision}
          onClose={() => setSelectedDecision(null)}
          setReloadKey={setReloadKey}
        />
      )}

      <p className="mt-6 text-center text-[12px] text-subtle-foreground">
        Data from canonical fixtures: decision_finance_pln_needs_review.json / decision_finance_pln_resolved.json
      </p>
    </div>
  );
}