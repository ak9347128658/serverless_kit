import { useCallback } from "react";
import { LogStreams } from "../../components/LogStreams";
import { LogViewer } from "../../components/LogViewer";
import { api } from "../api";
import { useConfirm } from "../components/Feedback";
import { Section } from "../components/Forms";
import { PageHeader } from "../components/Misc";
import { DeployedBadge, ServerSelect, useServerChoice } from "../components/ServerPicker";
import { href } from "../route";
import { useStudio } from "../store";
import { asObject } from "../util";

type LogParams = Parameters<typeof api.logs>[2];

/** Logs of the deployed target (top bar provider / stage): live tail, streams, delete. */
export function LogsView({ rest }: { rest: string[] }) {
  const { state, draft, meta } = useStudio();
  const target = state.target || { provider: "aws", stage: "dev" };
  const functions = Object.keys(asObject(draft.functions));
  const only = rest[0] && functions.includes(rest[0]) ? rest[0] : undefined;
  const name = meta.providers.find((p) => p.name === target.provider)?.display_name || target.provider;
  const onprem = target.provider === "onprem";
  const { servers, deployed, server, setServer } = useServerChoice(onprem, target.stage);
  return (
    <div className="page">
      <PageHeader
        title="Logs"
        subtitle={
          <>
            Logs of the functions deployed to <b>{name}</b> / <b>{target.stage}</b> (change in the top bar). They are read with this
            machine's credentials; see <a href={href("setup")}>Setup &amp; credentials</a> if they can't be read.
          </>
        }
      />
      {onprem && (
        <div className="card card-pad row-gap wrap deploy-machine">
          <b>Server</b>
          <ServerSelect value={server} onChange={setServer} servers={servers} deployed={deployed} />
          <DeployedBadge deployed={deployed} />
          <span className="muted small">
            {deployed?.cluster ? (
              <>
                This stage runs on cluster <b>{deployed.cluster}</b>. Logs from every replica are shown together. On Nomad each server is read
                directly, so a web replica's lines show with the main replica. CLI: <code className="mono">pskit logs -f</code>.
              </>
            ) : (
              <>
                Starts on the server this stage was deployed to. When its port (default 8080) is closed to this machine the logs are read over SSH. CLI:{" "}
                <code className="mono">pskit logs --server NAME</code>.
              </>
            )}
          </span>
        </div>
      )}
      <DeployedLogs provider={target.provider} stage={target.stage} functions={functions} only={only} server={onprem ? server || undefined : undefined} streams />
    </div>
  );
}

/** Live logs (and optionally the stream list with delete) for one deployment, or one function of it. */
export function DeployedLogs({
  provider,
  stage,
  functions,
  only,
  server,
  streams = false,
}: {
  provider: string;
  stage: string;
  functions: string[];
  only?: string;
  server?: string;
  streams?: boolean;
}) {
  const confirm = useConfirm();
  const fetchLogs = useCallback(
    (p: LogParams) => api.logs(provider, stage, only ? { ...p, function: only } : p, server),
    [provider, stage, only, server],
  );
  const fetchStreams = useCallback((fn?: string) => api.logStreams(provider, stage, only || fn, server), [provider, stage, only, server]);
  const deleteStreams = useCallback(
    (body: { ids?: string[]; all?: boolean; function?: string }) => api.deleteLogs(provider, stage, only ? { ...body, function: only } : body, server),
    [provider, stage, only, server],
  );
  const ask = useCallback(
    async (message: string) =>
      (await confirm({ title: "Delete logs?", body: message, confirmLabel: "Delete", danger: true })) === "confirm",
    [confirm],
  );
  const names = only ? [only] : functions;
  return (
    <div className="stack">
      <div className="card">
        <LogViewer key={`${provider}-${stage}-${only || ""}-${server || ""}`} fetchLogs={fetchLogs} functions={names} />
      </div>
      {streams && (
        <Section title="Stored logs" hint="list, select and delete" defaultOpen={false}>
          <LogStreams key={`${provider}-${stage}-${only || ""}-${server || ""}`} fetchStreams={fetchStreams} deleteStreams={deleteStreams} functions={names} confirm={ask} />
        </Section>
      )}
    </div>
  );
}
