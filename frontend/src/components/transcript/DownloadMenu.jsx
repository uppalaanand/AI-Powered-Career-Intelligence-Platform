import { useState } from 'react';
import { Download } from 'lucide-react';
import { Button } from '../ui';
import { transcriptionApi } from '../../services/api';
import { DOWNLOAD_FORMATS } from '../../utils/constants';
import { useToast } from '../../hooks/useToast';

/** Download buttons. Each one fetches the real stored transcript from the API. */
export function DownloadMenu({ meetingId }) {
  const [busyFormat, setBusyFormat] = useState(null);
  const toast = useToast();

  const download = async (format) => {
    setBusyFormat(format);
    try {
      const filename = await transcriptionApi.download(meetingId, format);
      toast.success(`Downloaded ${filename}`);
    } catch (error) {
      toast.error(error.displayMessage || 'The download failed.');
    } finally {
      setBusyFormat(null);
    }
  };

  return (
    <div className="btn-row">
      {DOWNLOAD_FORMATS.map((format) => (
        <Button
          key={format.id}
          size="sm"
          icon={Download}
          loading={busyFormat === format.id}
          onClick={() => download(format.id)}
        >
          {format.label} <span className="text-muted">{format.hint}</span>
        </Button>
      ))}
    </div>
  );
}
