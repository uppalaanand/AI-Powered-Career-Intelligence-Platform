import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DropZone, SelectedFile } from '../components/upload/DropZone';

const extensions = ['mp3', 'wav', 'mp4'];

function makeFile(name, size, type = 'audio/mpeg') {
  const file = new File(['x'], name, { type });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

describe('DropZone', () => {
  it('accepts a supported file', () => {
    const onSelect = vi.fn();
    render(<DropZone extensions={extensions} maxSizeMb={200} onSelect={onSelect} />);

    fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [makeFile('meeting.mp3', 1024 * 1024)] },
    });

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].name).toBe('meeting.mp3');
  });

  it('rejects an unsupported extension with a useful message', () => {
    const onSelect = vi.fn();
    const onReject = vi.fn();
    render(
      <DropZone extensions={extensions} maxSizeMb={200} onSelect={onSelect} onReject={onReject} />
    );

    fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [makeFile('report.pdf', 2048, 'application/pdf')] },
    });

    expect(onSelect).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith(expect.stringContaining('not supported'));
  });

  it('rejects an oversized file', () => {
    const onReject = vi.fn();
    render(<DropZone extensions={extensions} maxSizeMb={10} onReject={onReject} />);

    fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [makeFile('huge.mp4', 50 * 1024 * 1024, 'video/mp4')] },
    });

    expect(onReject).toHaveBeenCalledWith(expect.stringContaining('limit is 10 MB'));
  });

  it('rejects an empty file', () => {
    const onReject = vi.fn();
    render(<DropZone extensions={extensions} onReject={onReject} />);

    fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [makeFile('empty.wav', 0, 'audio/wav')] },
    });

    expect(onReject).toHaveBeenCalledWith('That file is empty.');
  });

  it('shows the chosen file with its size', () => {
    render(<SelectedFile file={makeFile('standup.mp4', 2 * 1024 * 1024)} />);
    expect(screen.getByText('standup.mp4')).toBeInTheDocument();
    expect(screen.getByText('2.0 MB')).toBeInTheDocument();
  });
});
