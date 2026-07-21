import { useRef, useEffect, useState, useCallback } from 'react';

export default function EventFeed({ events, onClear }) {
  const scrollRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events, autoScroll]);

  const toggleAutoScroll = useCallback(() => {
    setAutoScroll((v) => !v);
  }, []);

  return (
    <section className="card card--events">
      <div className="card-head">
        <h2>Event Feed</h2>
        <div className="card-actions">
          <button className="btn-sm" onClick={onClear}>
            Clear
          </button>
          <button
            className={`btn-sm ${autoScroll ? 'active' : ''}`}
            onClick={toggleAutoScroll}
          >
            Auto ↓
          </button>
        </div>
      </div>
      <div className="card-body" ref={scrollRef}>
        <div className="feed">
          {events.length === 0 ? (
            <div className="empty-state">
              <p className="empty-title">No events yet</p>
              <p className="empty-hint">
                Run <code>python scripts/run_demo.py</code>
              </p>
            </div>
          ) : (
            events.map((ev, i) => <EventRow key={i} ev={ev} />)
          )}
        </div>
      </div>
    </section>
  );
}

function EventRow({ ev }) {
  const hlcStr = ev.hlc ? `${ev.hlc.l}:${ev.hlc.c}` : '';

  return (
    <div className="ev">
      <span className={`ev-transport ${ev.transport || ''}`}>
        {ev.transport}
      </span>
      <span className="ev-agent">{ev.agent_id}</span>
      <span className="ev-kind">{ev.kind}</span>
      <span className="ev-resource">{ev.resource_id || '—'}</span>
      <span className="ev-hlc">{hlcStr}</span>
    </div>
  );
}
