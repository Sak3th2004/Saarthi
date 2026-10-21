import { useId } from 'react';

export function CompanionFace({ mode }: { mode: string }) {
  const id = useId().replaceAll(':', '');
  const paint = (name: string) => `url(#${id}-${name})`;
  return <div className={`companion-face ${mode}`} aria-hidden="true">
    <svg viewBox="0 0 200 180" focusable="false">
      <defs>
        <radialGradient id={`${id}-shell`} cx=".3" cy=".2" r=".9"><stop stopColor="#fff"/><stop offset=".45" stopColor="#e3f4f5"/><stop offset=".8" stopColor="#9dc6d1"/><stop offset="1" stopColor="#5b879e"/></radialGradient>
        <linearGradient id={`${id}-screen`} x1="0" y1="0" x2=".8" y2="1"><stop stopColor="#28556c"/><stop offset=".5" stopColor="#102d42"/><stop offset="1" stopColor="#061e32"/></linearGradient>
        <linearGradient id={`${id}-shine`} x1="0" y1="0" x2=".4" y2="1"><stop stopColor="#fff" stopOpacity=".25"/><stop offset="1" stopColor="#fff" stopOpacity="0"/></linearGradient>
        <radialGradient id={`${id}-shadow`}><stop stopColor="#173d52" stopOpacity=".22"/><stop offset="1" stopColor="#173d52" stopOpacity="0"/></radialGradient>
      </defs>
      <ellipse className="companion-shadow" cx="100" cy="166" rx="67" ry="10" fill={paint('shadow')}/>
      <ellipse className="companion-platform" cx="100" cy="156" rx="57" ry="9" fill="none" stroke="#81b0bd" strokeWidth="1"/>
      <path d="M100 38V23" fill="none" stroke="#326a79" strokeWidth="5" strokeLinecap="round"/>
      <circle className="companion-light" cx="100" cy="18" r="7" fill="#087b86"/>
      <rect x="25" y="76" width="18" height="39" rx="9" fill="#96c3d0"/>
      <rect x="157" y="76" width="18" height="39" rx="9" fill="#96c3d0"/>
      <rect x="36" y="40" width="128" height="110" rx="43" fill={paint('shell')} stroke="#7ba9b8"/>
      <path d="M43 74Q47 45 77 45H118" fill="none" stroke="#fff" strokeOpacity=".8" strokeWidth="2" strokeLinecap="round"/>
      <rect x="47" y="55" width="106" height="79" rx="31" fill={paint('screen')} stroke="#759baa" strokeWidth="2"/>
      <path d="M59 64Q79 53 127 63Q145 65 146 83Q100 71 54 103V83Q54 70 59 64Z" fill={paint('shine')}/>
      <g className="companion-eyes" fill="#caf8f4"><rect x="68" y="78" width="13" height="21" rx="6.5"/><rect x="119" y="78" width="13" height="21" rx="6.5"/></g>
      <path className="companion-mouth" d="M89 111Q100 120 111 111" fill="none" stroke="#8ae3de" strokeWidth="3" strokeLinecap="round"/>
      <circle cx="62" cy="106" r="4" fill="#4ea5b1" opacity=".55"/><circle cx="138" cy="106" r="4" fill="#4ea5b1" opacity=".55"/>
    </svg>
  </div>;
}
