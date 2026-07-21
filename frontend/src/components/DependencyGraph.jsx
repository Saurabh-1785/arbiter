import { useRef, useEffect, useState } from 'react';
import * as d3 from 'd3';

const EDGE_COLORS = { ww: '#B8924A', wr: '#5A9E6F', rw: '#C45B5B' };
const NODE_COLOR_DARK = '#E8E8E8';
const NODE_COLOR_LIGHT = '#1A1A1B';

export default function DependencyGraph({ graph, theme }) {
  const svgRef = useRef(null);
  const wrapRef = useRef(null);
  const zoomRef = useRef(null);
  const [zoomLevel, setZoomLevel] = useState(100);

  const { nodes, edges } = graph || { nodes: [], edges: [] };
  const hasData = nodes && nodes.length > 0;

  useEffect(() => {
    if (!hasData || !svgRef.current || !wrapRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const rect = wrapRef.current.getBoundingClientRect();
    const w = rect.width || 400;
    const h = rect.height || 300;
    svg.attr('viewBox', `0 0 ${w} ${h}`);

    const viewport = svg.append('g').attr('class', 'graph-viewport');
    const zoom = d3.zoom()
      .scaleExtent([0.35, 12])
      .on('zoom', (event) => {
        viewport.attr('transform', event.transform);
        setZoomLevel(Math.round(event.transform.k * 100));
      });
    zoomRef.current = zoom;
    svg.call(zoom).on('dblclick.zoom', null);

    // Arrow markers
    const defs = svg.append('defs');
    ['ww', 'wr', 'rw'].forEach((t) => {
      defs
        .append('marker')
        .attr('id', `arr-${t}`)
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 26)
        .attr('refY', 0)
        .attr('markerWidth', 7)
        .attr('markerHeight', 7)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-5L10,0L0,5')
        .attr('class', `arrow-${t}`);
    });

    const nodeData = nodes.map((n) => ({ id: n.id }));
    const edgeData = (edges || []).map((e, i) => ({
      source: e.source,
      target: e.target,
      type: e.type,
      resource_id: e.resource_id,
      id: `e-${i}`,
    }));

    const sim = d3
      .forceSimulation(nodeData)
      .force('link', d3.forceLink(edgeData).id((d) => d.id).distance(110))
      .force('charge', d3.forceManyBody().strength(-250))
      .force('center', d3.forceCenter(w / 2, h / 2))
      .force('collide', d3.forceCollide(28));

    // Edges
    const links = viewport
      .append('g')
      .selectAll('line')
      .data(edgeData)
      .enter()
      .append('line')
      .attr('class', (d) => `g-edge ${d.type}`)
      .attr('marker-end', (d) => `url(#arr-${d.type})`);

    // Edge labels
    const labels = viewport
      .append('g')
      .selectAll('text')
      .data(edgeData)
      .enter()
      .append('text')
      .attr('class', 'g-label')
      .text((d) => d.type.toUpperCase());

    // Node groups
    const textColor = theme === 'dark' ? NODE_COLOR_DARK : NODE_COLOR_LIGHT;
    const circleFill = theme === 'dark' ? '#2A2A2A' : '#F7F7F8';
    const circleStroke = theme === 'dark' ? '#4A4A4D' : '#E0E0E2';

    const ng = viewport
      .append('g')
      .selectAll('g')
      .data(nodeData)
      .enter()
      .append('g')
      .attr('class', 'g-node')
      .call(
        d3
          .drag()
          .on('start', (event, d) => {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on('drag', (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on('end', (event, d) => {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );

    ng.append('circle')
      .attr('r', 18)
      .attr('fill', circleFill)
      .attr('stroke', circleStroke)
      .attr('stroke-width', 1.5);

    ng.append('text')
      .attr('dy', 4)
      .attr('text-anchor', 'middle')
      .attr('fill', textColor)
      .text((d) => d.id.replace('agent-', ''));

    sim.on('tick', () => {
      links
        .attr('x1', (d) => d.source.x)
        .attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x)
        .attr('y2', (d) => d.target.y);
      labels
        .attr('x', (d) => (d.source.x + d.target.x) / 2)
        .attr('y', (d) => (d.source.y + d.target.y) / 2 - 8);
      ng.attr('transform', (d) => `translate(${d.x},${d.y})`);
    });

    return () => sim.stop();
  }, [hasData, nodes, edges, theme]);

  const adjustZoom = (amount) => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current).transition().duration(180).call(zoomRef.current.scaleBy, amount);
  };

  const resetZoom = () => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current).transition().duration(220).call(zoomRef.current.transform, d3.zoomIdentity);
  };

  return (
    <section className="card card--graph">
      <div className="card-head">
        <h2>Dependency Graph</h2>
        <div className="graph-head-actions">
          <div className="legend" aria-label="Graph edge legend">
            <span className="legend-chip legend-ww">WW</span>
            <span className="legend-chip legend-wr">WR</span>
            <span className="legend-chip legend-rw">RW</span>
          </div>
          <div className="graph-controls" aria-label="Graph zoom controls">
            <button className="graph-control" onClick={() => adjustZoom(1.5)} aria-label="Zoom in">+</button>
            <button className="graph-control graph-control--reset" onClick={resetZoom}>{zoomLevel}%</button>
            <button className="graph-control" onClick={() => adjustZoom(0.67)} aria-label="Zoom out">-</button>
          </div>
        </div>
      </div>
      <div className="card-body card-body--graph" ref={wrapRef}>
        {!hasData ? (
          <div className="empty-state">
            <p className="empty-title">No graph data</p>
          </div>
        ) : (
          <div className="graph-wrap">
            <svg ref={svgRef} />
            <span className="graph-hint">Scroll to zoom · drag to explore</span>
          </div>
        )}
      </div>
    </section>
  );
}
