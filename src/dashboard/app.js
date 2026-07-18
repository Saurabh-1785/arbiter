/**
 * ARBITER Dashboard — WebSocket client and D3.js visualization
 *
 * Connects to the verifier service over WebSocket, receives typed messages
 * (event, state_update, graph_update, violation), and renders them in
 * the four dashboard panels in real time.
 */

// ============================================================
// State
// ============================================================
const state = {
    ws: null,
    events: [],
    states: {},       // resource_id -> state data
    graph: { nodes: [], edges: [] },
    violations: [],
    agents: new Set(),
    autoScroll: true,
    simulation: null,  // D3 force simulation
};

// Agent color map
const AGENT_COLORS = {
    'agent-A': '#3b82f6',
    'agent-B': '#10b981',
    'agent-C': '#f97316',
    'agent-D': '#ec4899',
};

// Edge type colors
const EDGE_COLORS = {
    'ww': '#60a5fa',
    'wr': '#34d399',
    'rw': '#f87171',
};

// State flow order
const STATE_FLOW = ['Idle', 'Claimed', 'InProgress', 'AwaitingAck', 'Acked', 'Escalated', 'Violated'];

// ============================================================
// WebSocket Connection
// ============================================================
function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        updateConnectionStatus(true);
        console.log('[ARBITER] WebSocket connected');
    };

    state.ws.onclose = () => {
        updateConnectionStatus(false);
        console.log('[ARBITER] WebSocket disconnected — reconnecting in 2s');
        setTimeout(connect, 2000);
    };

    state.ws.onerror = (err) => {
        console.error('[ARBITER] WebSocket error:', err);
    };

    state.ws.onmessage = (evt) => {
        try {
            const msg = JSON.parse(evt.data);
            handleMessage(msg);
        } catch (e) {
            console.error('[ARBITER] Failed to parse message:', e);
        }
    };
}

function updateConnectionStatus(connected) {
    const dot = document.querySelector('.status-dot');
    const text = document.querySelector('.status-text');
    if (connected) {
        dot.className = 'status-dot connected';
        text.textContent = 'Connected';
    } else {
        dot.className = 'status-dot disconnected';
        text.textContent = 'Disconnected';
    }
}

// ============================================================
// Message Handler (Patch §7: typed WebSocket protocol)
// ============================================================
function handleMessage(msg) {
    switch (msg.type) {
        case 'event':
            handleEvent(msg.data);
            break;
        case 'state_update':
            handleStateUpdate(msg.data);
            break;
        case 'graph_update':
            handleGraphUpdate(msg.data);
            break;
        case 'violation':
            handleViolation(msg.data);
            break;
        default:
            console.warn('[ARBITER] Unknown message type:', msg.type);
    }
}

// ============================================================
// Panel 1: Event Feed
// ============================================================
function handleEvent(data) {
    state.events.push(data);
    state.agents.add(data.agent_id);

    // Update stats
    document.getElementById('event-count').textContent = state.events.length;
    document.getElementById('agent-count').textContent = state.agents.size;

    // Render event item
    const feed = document.getElementById('event-feed');

    // Remove empty state
    const empty = feed.querySelector('.event-feed-empty');
    if (empty) empty.remove();

    const item = document.createElement('div');
    item.className = 'event-item';

    const transportClass = data.transport || 'stdout';
    const agentClass = data.agent_id ? data.agent_id.replace('_', '-') : '';

    item.innerHTML = `
        <span class="event-transport ${transportClass}">${data.transport}</span>
        <span class="event-agent ${agentClass}">${data.agent_id}</span>
        <span class="event-kind">${data.kind}</span>
        <span class="event-resource">${data.resource_id || '—'}</span>
        <span class="event-hlc">${data.hlc ? `${data.hlc.l}:${data.hlc.c}` : ''}</span>
    `;

    feed.appendChild(item);

    // Auto-scroll
    if (state.autoScroll) {
        const panelBody = feed.closest('.panel-body');
        if (panelBody) {
            panelBody.scrollTop = panelBody.scrollHeight;
        }
    }
}

// ============================================================
// Panel 2: State Machine Status
// ============================================================
function handleStateUpdate(data) {
    state.states[data.resource_id] = data;
    renderStateMachines();
}

function renderStateMachines() {
    const container = document.getElementById('state-machines');

    // Remove empty state
    const empty = container.querySelector('.state-empty');
    if (empty) empty.remove();

    container.innerHTML = '';

    for (const [resourceId, stateData] of Object.entries(state.states)) {
        const card = document.createElement('div');
        card.className = 'state-machine-card';

        const ownerText = stateData.current_owner
            ? `Owner: <span class="event-agent ${stateData.current_owner.replace('_', '-')}">${stateData.current_owner}</span>`
            : 'No owner';

        let flowHtml = '';
        for (let i = 0; i < STATE_FLOW.length; i++) {
            const s = STATE_FLOW[i];
            const isActive = s === stateData.state;
            flowHtml += `<span class="state-node ${s} ${isActive ? 'active' : ''}">${s}</span>`;
            if (i < STATE_FLOW.length - 1) {
                flowHtml += '<span class="state-arrow">→</span>';
            }
        }

        card.innerHTML = `
            <div class="state-machine-header">
                <span class="state-machine-resource">${resourceId}</span>
                <span class="state-machine-owner">${ownerText}</span>
            </div>
            <div class="state-flow">${flowHtml}</div>
        `;

        container.appendChild(card);
    }
}

// ============================================================
// Panel 3: Dependency Graph (D3.js)
// ============================================================
function handleGraphUpdate(data) {
    state.graph = data;
    renderGraph();
}

function renderGraph() {
    const container = document.getElementById('graph-container');
    const svg = d3.select('#dependency-graph');
    svg.selectAll('*').remove();

    const width = container.clientWidth || 400;
    const height = container.clientHeight || 300;

    svg.attr('viewBox', `0 0 ${width} ${height}`);

    const { nodes, edges } = state.graph;

    if (!nodes || nodes.length === 0) return;

    // Define arrow markers
    const defs = svg.append('defs');
    ['ww', 'wr', 'rw'].forEach(type => {
        defs.append('marker')
            .attr('id', `arrow-${type}`)
            .attr('viewBox', '0 -5 10 10')
            .attr('refX', 28)
            .attr('refY', 0)
            .attr('markerWidth', 8)
            .attr('markerHeight', 8)
            .attr('orient', 'auto')
            .append('path')
            .attr('d', 'M0,-5L10,0L0,5')
            .attr('class', `arrow-${type}`);
    });

    // Process nodes with agent colors
    const nodeData = nodes.map(n => ({
        id: n.id,
        color: AGENT_COLORS[n.id] || '#64748b',
    }));

    // Process edges
    const edgeData = (edges || []).map((e, i) => ({
        source: e.source,
        target: e.target,
        type: e.type,
        resource_id: e.resource_id,
        id: `edge-${i}`,
    }));

    // Force simulation
    const simulation = d3.forceSimulation(nodeData)
        .force('link', d3.forceLink(edgeData).id(d => d.id).distance(120))
        .force('charge', d3.forceManyBody().strength(-300))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius(30));

    // Render edges
    const links = svg.append('g')
        .selectAll('line')
        .data(edgeData)
        .enter()
        .append('line')
        .attr('class', d => `graph-edge ${d.type}`)
        .attr('marker-end', d => `url(#arrow-${d.type})`);

    // Edge labels
    const edgeLabels = svg.append('g')
        .selectAll('text')
        .data(edgeData)
        .enter()
        .append('text')
        .attr('class', 'graph-edge-label')
        .text(d => `${d.type.toUpperCase()}: ${d.resource_id || ''}`);

    // Render nodes
    const nodeGroups = svg.append('g')
        .selectAll('g')
        .data(nodeData)
        .enter()
        .append('g')
        .attr('class', 'graph-node')
        .call(d3.drag()
            .on('start', (event, d) => {
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x; d.fy = d.y;
            })
            .on('drag', (event, d) => {
                d.fx = event.x; d.fy = event.y;
            })
            .on('end', (event, d) => {
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null; d.fy = null;
            })
        );

    nodeGroups.append('circle')
        .attr('r', 18)
        .attr('fill', d => d.color)
        .attr('fill-opacity', 0.15)
        .attr('stroke', d => d.color)
        .attr('stroke-opacity', 0.8);

    nodeGroups.append('text')
        .attr('dy', 4)
        .attr('text-anchor', 'middle')
        .text(d => d.id.replace('agent-', ''));

    // Tick handler
    simulation.on('tick', () => {
        links
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);

        edgeLabels
            .attr('x', d => (d.source.x + d.target.x) / 2)
            .attr('y', d => (d.source.y + d.target.y) / 2 - 8);

        nodeGroups.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    state.simulation = simulation;
}

// ============================================================
// Panel 4: Violations & Revocations
// ============================================================
function handleViolation(data) {
    state.violations.push(data);
    document.getElementById('violation-count').textContent = state.violations.length;
    renderViolation(data);
}

function renderViolation(data) {
    const container = document.getElementById('violations-list');

    // Remove empty state
    const empty = container.querySelector('.violations-empty');
    if (empty) empty.remove();

    const item = document.createElement('div');
    const isCycle = data.type === 'dependency_cycle';
    item.className = `violation-item ${isCycle ? 'cycle' : ''}`;

    const agentClass = data.agent_id ? data.agent_id.replace('_', '-') : '';
    const revokedScopes = data.revoked_scopes && data.revoked_scopes.length > 0
        ? `<div class="violation-revoked">🔒 Revoked: ${data.revoked_scopes.join(', ')}</div>`
        : '';

    item.innerHTML = `
        <div class="violation-header">
            <span class="violation-type ${data.type}">${data.type.replace(/_/g, ' ')}</span>
            <span class="violation-agent ${agentClass}">${data.agent_id}</span>
        </div>
        <div class="violation-reason">${data.reason || 'Unknown violation'}</div>
        ${revokedScopes}
    `;

    container.appendChild(item);

    // Auto-scroll
    const panelBody = container.closest('.panel-body');
    if (panelBody) {
        panelBody.scrollTop = panelBody.scrollHeight;
    }
}

// ============================================================
// Controls
// ============================================================
document.getElementById('clear-events')?.addEventListener('click', () => {
    state.events = [];
    const feed = document.getElementById('event-feed');
    feed.innerHTML = `
        <div class="event-feed-empty">
            <span class="empty-icon">◇</span>
            <p>Events cleared</p>
        </div>
    `;
    document.getElementById('event-count').textContent = '0';
});

document.getElementById('auto-scroll-toggle')?.addEventListener('click', (e) => {
    state.autoScroll = !state.autoScroll;
    e.target.style.opacity = state.autoScroll ? '1' : '0.4';
});

// Handle window resize for graph
window.addEventListener('resize', () => {
    if (state.graph.nodes && state.graph.nodes.length > 0) {
        renderGraph();
    }
});

// ============================================================
// Initialize
// ============================================================
connect();
console.log('[ARBITER] Dashboard initialized');
