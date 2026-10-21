import { useCallback } from "react";
import { api } from "../api";
import type { Deployment, ResourceRecord } from "../types";
import { Badge, Chip } from "../components/Badge";
import { DataTable, type Column } from "../components/DataTable";
import { EmptyState } from "../components/Feedback";
import { JsonView } from "../components/JsonView";
import { LogViewer } from "../components/LogViewer";

export function LogsTab({ deployment, deploymentKey, live }: { deployment: Deployment; deploymentKey: string; live: boolean }) {
  const fetchLogs = useCallback(
    (p: Parameters<typeof api.logs>[1]) => api.logs(deploymentKey, p),
    [deploymentKey],
  );
  if (!live)
    return (
      <EmptyState title="Live data needs the dashboard server" icon="⚡">
        Run <code>pskit dashboard --serve</code> to tail logs from a running on-premises host.
      </EmptyState>
    );
  return <LogViewer key={deploymentKey} fetchLogs={fetchLogs} functions={(deployment.functions || []).map((f) => f.name)} />;
}

export function ResourcesTab({ deployment }: { deployment: Deployment }) {
  const rows = deployment.resources || [];
  const columns: Column<ResourceRecord>[] = [
    { key: "name", header: "Resource", sortValue: (r) => r.name, render: (r) => <b>{r.name}</b> },
    { key: "type", header: "Type", sortValue: (r) => r.type, render: (r) => <Badge>{r.type}</Badge> },
    {
      key: "mode",
      header: "Mode",
      sortValue: (r) => (r.external ? 1 : 0),
      render: (r) => <Badge tone={r.external ? "warn" : "ok"}>{r.external ? "external" : "managed"}</Badge>,
    },
    { key: "native", header: "Native name", render: (r) => <span className="mono">{r.native_name || "—"}</span> },
    {
      key: "functions",
      header: "Bound to",
      render: (r) =>
        (r.functions || []).length ? (
          <div className="chips">
            {(r.functions || []).map((f) => (
              <Chip key={f}>{f}</Chip>
            ))}
          </div>
        ) : (
          <span className="muted">—</span>
        ),
    },
    {
      key: "keys",
      header: "Connection keys",
      render: (r) =>
        (r.connection_keys || []).length ? (
          <div className="chips">
            {(r.connection_keys || []).map((k) => (
              <Chip key={k}>{k}</Chip>
            ))}
          </div>
        ) : (
          <span className="muted">—</span>
        ),
    },
  ];
  return (
    <div className="card">
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.name}
        emptyTitle="No resources"
        emptyHint={
          <>
            Attach storage, queues, databases or other services under <code>resources:</code> in pskit.yml.
          </>
        }
      />
    </div>
  );
}

export function OutputsTab({ deployment }: { deployment: Deployment }) {
  const outputs = deployment.outputs || {};
  return (
    <div className="stack">
      <div className="card card-pad">
        <h4>Terraform outputs</h4>
        {Object.keys(outputs).length ? <JsonView value={outputs} /> : <p className="muted">No extra outputs.</p>}
      </div>
      <div className="card card-pad">
        <h4>Artifact</h4>
        <JsonView value={deployment.artifact || {}} />
      </div>
    </div>
  );
}
