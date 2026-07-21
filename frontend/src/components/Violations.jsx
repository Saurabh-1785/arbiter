export default function Violations({ violations }) {
  return (
    <section className="card card--violations">
      <div className="card-head">
        <h2>Violations &amp; Revocations</h2>
      </div>
      <div className="card-body">
        {violations.length === 0 ? (
          <div className="empty-state">
            <p className="empty-title">All clear</p>
            <p className="empty-hint">No violations detected</p>
          </div>
        ) : (
          violations.map((v, i) => <ViolationCard key={i} data={v} />)
        )}
      </div>
    </section>
  );
}

function ViolationCard({ data }) {
  const isCycle = data.type === 'dependency_cycle';
  const revokedScopes = data.revoked_scopes || [];

  return (
    <div className={`vio ${isCycle ? 'cycle' : ''}`}>
      <div className="vio-header">
        <span className={`vio-badge ${data.type}`}>
          {data.type.replace(/_/g, ' ')}
        </span>
        <span className="vio-agent">{data.agent_id}</span>
      </div>
      <div className="vio-reason">{data.reason || 'Unknown violation'}</div>
      {revokedScopes.length > 0 && (
        <div className="vio-revoked">Revoked: {revokedScopes.join(', ')}</div>
      )}
    </div>
  );
}
