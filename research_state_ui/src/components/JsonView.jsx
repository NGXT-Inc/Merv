import { useState } from 'react';

/**
 * JsonView — a collapsible, JSON-native tree for reading structured tool
 * responses. Objects/arrays fold; long strings truncate with click-to-expand;
 * each container shows its size so you can gauge a payload at a glance.
 *
 * Deliberately dependency-free and read-only.
 */
export default function JsonView({ data, initialDepth = 2 }) {
  const [spread, setSpread] = useState(initialDepth);
  const [copied, setCopied] = useState(false);

  const copy = () => {
    try {
      navigator.clipboard?.writeText(JSON.stringify(data, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* clipboard may be unavailable */ }
  };

  return (
    <div className="jsonview">
      <div className="jsonview-bar">
        <button type="button" className="jsonview-btn" onClick={() => setSpread(999)}>expand all</button>
        <button type="button" className="jsonview-btn" onClick={() => setSpread(1)}>collapse</button>
        <span className="jsonview-bar-spacer" />
        <button type="button" className="jsonview-btn" onClick={copy}>{copied ? 'copied' : 'copy'}</button>
      </div>
      <div className="jsonview-body" key={spread}>
        <Node nodeKey={null} value={data} depth={0} initialDepth={spread} isRoot />
      </div>
    </div>
  );
}

function typeOf(v) {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  return typeof v;
}

function Node({ nodeKey, value, depth, initialDepth, isLast = true, isRoot = false }) {
  const type = typeOf(value);
  const isContainer = type === 'object' || type === 'array';
  const [open, setOpen] = useState(depth < initialDepth);

  const keyEl = nodeKey != null && (
    <span className="jv-key">{Array.isArray(nodeKey) ? nodeKey : `"${nodeKey}"`}<span className="jv-punct">: </span></span>
  );

  if (!isContainer) {
    return (
      <div className="jv-row" style={{ paddingLeft: depth * 14 }}>
        {keyEl}
        <Leaf value={value} type={type} />
        {!isLast && <span className="jv-punct">,</span>}
      </div>
    );
  }

  const entries = type === 'array'
    ? value.map((v, i) => [i, v])
    : Object.entries(value);
  const open_b = type === 'array' ? '[' : '{';
  const close_b = type === 'array' ? ']' : '}';
  const count = entries.length;
  const summary = type === 'array' ? `${count} item${count === 1 ? '' : 's'}` : `${count} key${count === 1 ? '' : 's'}`;

  return (
    <div className="jv-node">
      <div className="jv-row jv-row--clickable" style={{ paddingLeft: depth * 14 }} onClick={() => setOpen(o => !o)}>
        <span className={`jv-twist${open ? ' open' : ''}`}>▸</span>
        {keyEl}
        <span className="jv-punct">{open_b}</span>
        {!open && (
          <>
            <span className="jv-count">{summary}</span>
            <span className="jv-punct">{close_b}</span>
            {!isLast && <span className="jv-punct">,</span>}
          </>
        )}
        {open && <span className="jv-count jv-count--faint">{summary}</span>}
      </div>
      {open && (
        <>
          {entries.map(([k, v], i) => (
            <Node
              key={k}
              nodeKey={k}
              value={v}
              depth={depth + 1}
              initialDepth={initialDepth}
              isLast={i === entries.length - 1}
            />
          ))}
          <div className="jv-row" style={{ paddingLeft: depth * 14 }}>
            <span className="jv-punct">{close_b}</span>
            {!isLast && <span className="jv-punct">,</span>}
          </div>
        </>
      )}
    </div>
  );
}

const STRING_PREVIEW = 220;

function Leaf({ value, type }) {
  const [expanded, setExpanded] = useState(false);
  if (type === 'string') {
    const long = value.length > STRING_PREVIEW;
    const shown = long && !expanded ? value.slice(0, STRING_PREVIEW) : value;
    return (
      <span className="jv-string">
        "{shown}{long && !expanded && '…'}"
        {long && (
          <button type="button" className="jv-more" onClick={(e) => { e.stopPropagation(); setExpanded(x => !x); }}>
            {expanded ? 'less' : `+${value.length - STRING_PREVIEW} chars`}
          </button>
        )}
      </span>
    );
  }
  if (type === 'number') return <span className="jv-number">{String(value)}</span>;
  if (type === 'boolean') return <span className="jv-bool">{String(value)}</span>;
  return <span className="jv-null">null</span>;
}
