import { useCallback, useRef, useState } from 'react';
import { FileAudio, UploadCloud } from 'lucide-react';
import { formatBytes } from '../../utils/format';

/**
 * Drag-and-drop file picker with client-side pre-checks.
 *
 * The browser check is a courtesy, not the gate: the backend re-validates every
 * file properly. Catching an obviously wrong file here just saves the user a
 * pointless upload.
 */
export function DropZone({ accept, extensions = [], maxSizeMb = 200, disabled = false, onSelect, onReject }) {
  const [isOver, setIsOver] = useState(false);
  const inputRef = useRef(null);

  const validate = useCallback(
    (file) => {
      const extension = file.name.split('.').pop()?.toLowerCase() || '';
      if (extensions.length && !extensions.includes(extension)) {
        return `'.${extension}' files are not supported. Use one of: ${extensions.join(', ')}.`;
      }
      if (file.size === 0) return 'That file is empty.';
      if (file.size > maxSizeMb * 1024 * 1024) {
        return `That file is ${formatBytes(file.size)}. The limit is ${maxSizeMb} MB.`;
      }
      return null;
    },
    [extensions, maxSizeMb]
  );

  const handleFiles = useCallback(
    (files) => {
      const file = files?.[0];
      if (!file) return;
      const problem = validate(file);
      if (problem) {
        onReject?.(problem);
        return;
      }
      onSelect?.(file);
    },
    [validate, onSelect, onReject]
  );

  const openPicker = () => {
    if (!disabled) inputRef.current?.click();
  };

  return (
    <div
      className={`dropzone ${isOver ? 'is-over' : ''} ${disabled ? 'is-disabled' : ''}`.trim()}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setIsOver(true);
      }}
      onDragLeave={() => setIsOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setIsOver(false);
        if (!disabled) handleFiles(event.dataTransfer.files);
      }}
      onClick={openPicker}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openPicker();
        }
      }}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      aria-label="Choose a recording to upload"
    >
      <UploadCloud size={30} className="dropzone-icon" aria-hidden="true" />
      <div className="dropzone-title">Drop a recording here, or click to browse</div>
      <div className="dropzone-hint">
        Audio or video, up to {maxSizeMb} MB
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="visually-hidden"
        onChange={(event) => {
          handleFiles(event.target.files);
          event.target.value = '';
        }}
        disabled={disabled}
        data-testid="file-input"
      />
    </div>
  );
}

export function SelectedFile({ file, onClear, disabled }) {
  if (!file) return null;
  return (
    <div className="file-chip">
      <FileAudio size={20} aria-hidden="true" style={{ color: 'var(--accent)' }} />
      <div style={{ minWidth: 0 }}>
        <div className="file-chip-name">{file.name}</div>
        <div className="file-chip-meta">{formatBytes(file.size)}</div>
      </div>
      {onClear && (
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onClear}
          disabled={disabled}
          style={{ marginLeft: 'auto' }}
        >
          Remove
        </button>
      )}
    </div>
  );
}
