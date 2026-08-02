/** Accessible on/off switch shared by the settings surfaces. */
export default function Switch({ checked, disabled, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`ui-switch${checked ? ' on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="ui-switch-knob" />
    </button>
  );
}
