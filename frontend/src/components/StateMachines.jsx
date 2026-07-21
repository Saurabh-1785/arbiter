const FLOW = ['Idle', 'Claimed', 'InProgress', 'AwaitingAck', 'Acked', 'Escalated', 'Violated'];

export default function StateMachines({ states }) {
  const entries = Object.entries(states);

  return (
    <section className="card card--states">
      <div className="card-head">
        <h2>State Machines</h2>
      </div>
      <div className="card-body">
        {entries.length === 0 ? (
          <div className="empty-state">
            <p className="empty-title">No resources tracked</p>
          </div>
        ) : (
          entries.map(([rid, data]) => (
            <StateCard key={rid} resourceId={rid} data={data} />
          ))
        )}
      </div>
    </section>
  );
}

function StateCard({ resourceId, data }) {
  return (
    <div className="sm-card">
      <div className="sm-header">
        <span className="sm-resource">{resourceId}</span>
        <span className="sm-owner">
          {data.current_owner ? `Owner: ${data.current_owner}` : 'No owner'}
        </span>
      </div>
      <div className="sm-flow">
        {FLOW.map((s, i) => (
          <span key={s}>
            {i > 0 && <span className="sm-arrow"> → </span>}
            <span className={`sm-node ${s} ${s === data.state ? 'active' : ''}`}>
              {s}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
