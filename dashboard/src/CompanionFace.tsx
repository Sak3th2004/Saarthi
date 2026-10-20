export function CompanionFace({ mode }: { mode: string }) {
  return <div className={`companion-face ${mode}`} aria-hidden="true">
    <svg viewBox="0 0 200 180" focusable="false">
      <defs><linearGradient id="companion-shell" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#ecf9f8"/><stop offset="1" stopColor="#a7d4dd"/></linearGradient></defs>
      <ellipse className="companion-shadow" cx="100" cy="164" rx="48" ry="7" fill="#173d52" opacity=".12"/>
      <path d="M100 38V23" fill="none" stroke="#326a79" strokeWidth="5" strokeLinecap="round"/>
      <circle className="companion-light" cx="100" cy="18" r="7" fill="#087b86"/>
      <rect x="25" y="76" width="18" height="39" rx="9" fill="#96c3d0"/>
      <rect x="157" y="76" width="18" height="39" rx="9" fill="#96c3d0"/>
      <rect x="36" y="40" width="128" height="110" rx="43" fill="url(#companion-shell)" stroke="#7ba9b8"/>
      <rect x="48" y="56" width="104" height="77" rx="30" fill="#12384d"/>
      <g className="companion-eyes" fill="#caf8f4"><rect x="68" y="78" width="13" height="21" rx="6.5"/><rect x="119" y="78" width="13" height="21" rx="6.5"/></g>
      <path className="companion-mouth" d="M89 111Q100 120 111 111" fill="none" stroke="#8ae3de" strokeWidth="3" strokeLinecap="round"/>
      <circle cx="62" cy="106" r="4" fill="#4ea5b1" opacity=".55"/><circle cx="138" cy="106" r="4" fill="#4ea5b1" opacity=".55"/>
    </svg>
  </div>;
}
