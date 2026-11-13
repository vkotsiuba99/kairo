import React from 'react';

/* Спільні примітиви інтерфейсу Kairo. */

export const Panel: React.FC<{
  children: React.ReactNode;
  className?: string;
  quiet?: boolean;
}> = ({ children, className = '', quiet }) => (
  <section className={`${quiet ? 'panel-quiet' : 'panel'} ${className}`}>{children}</section>
);

export const SectionHead: React.FC<{
  eyebrow?: string;
  title: string;
  note?: string;
  right?: React.ReactNode;
}> = ({ eyebrow, title, note, right }) => (
  <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
    <div className="max-w-2xl">
      {eyebrow && <div className="eyebrow mb-2">{eyebrow}</div>}
      <h2 className="dim-title text-3xl md:text-[2.1rem]">{title}</h2>
      {note && <p className="text-dim text-sm mt-2 leading-relaxed">{note}</p>}
    </div>
    {right}
  </div>
);

export const Field: React.FC<{
  label: string;
  hint?: string;
  children: React.ReactNode;
}> = ({ label, hint, children }) => (
  <label className="block">
    <span className="block text-[0.8rem] font-semibold text-butter mb-1.5 tracking-wide">{label}</span>
    {children}
    {hint && <span className="block text-xs text-faint mt-1.5 leading-snug">{hint}</span>}
  </label>
);

export const Spinner: React.FC<{ className?: string }> = ({ className = '' }) => (
  <span
    className={`spin inline-block rounded-full border-2 border-current border-t-transparent ${className}`}
    style={{ width: '1em', height: '1em' }}
    aria-hidden
  />
);

export const Stat: React.FC<{
  label: string;
  value: React.ReactNode;
  note?: string;
}> = ({ label, value, note }) => (
  <div className="convex px-6 py-5">
    <div className="text-[0.7rem] uppercase tracking-[0.16em] text-gold/80 font-mono">{label}</div>
    <div className="num dim-title-sm text-4xl mt-2 leading-none">{value}</div>
    {note && <div className="text-xs text-faint mt-2">{note}</div>}
  </div>
);

export const Notice: React.FC<{
  kind?: 'info' | 'good' | 'bad';
  children: React.ReactNode;
}> = ({ kind = 'info', children }) => {
  const tone =
    kind === 'good'
      ? 'border-gold/50 text-butter'
      : kind === 'bad'
        ? 'border-brick text-brick'
        : 'border-[color:var(--hairline-strong)] text-dim';
  return (
    <div className={`rise text-sm px-4 py-3 border-l-2 bg-[#eef3f9] ${tone}`}>{children}</div>
  );
};

export const Modal: React.FC<{
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  wide?: boolean;
}> = ({ open, onClose, title, children, footer, wide }) => {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-[#1e2530]/35 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={`panel rise w-full ${wide ? 'max-w-3xl' : 'max-w-lg'} max-h-[88vh] overflow-y-auto`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-7 pt-6 pb-4 border-b border-[color:var(--hairline)]">
          <h3 className="dim-title text-2xl">{title}</h3>
        </div>
        <div className="px-7 py-6">{children}</div>
        {footer && (
          <div className="px-7 py-5 border-t border-[color:var(--hairline)] flex justify-end gap-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};

export const Empty: React.FC<{ title: string; note?: string; action?: React.ReactNode }> = ({
  title,
  note,
  action,
}) => (
  <div className="py-16 text-center">
    <div className="text-2xl font-[family-name:var(--font-display)] text-dim dim-title-sm">{title}</div>
    {note && <p className="text-sm text-faint mt-2 max-w-md mx-auto">{note}</p>}
    {action && <div className="mt-6 flex justify-center">{action}</div>}
  </div>
);
