import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plug, Video, Download } from 'lucide-react';
import { Button, Card, Badge, Alert, Spinner } from '../components/ui';
import { integrationApi, meetingApi } from '../services/api';
import { useToast } from '../hooks/useToast';
import { formatDate, formatDuration } from '../utils/format';

export function IntegrationsPage() {
  const toast = useToast();
  const [zoomRecordings, setZoomRecordings] = useState(null);
  const [googleRecordings, setGoogleRecordings] = useState(null);
  const [loading, setLoading] = useState({ zoom: false, google: false });
  const [importing, setImporting] = useState({});

  const handleConnectZoom = async () => {
    try {
      setLoading(l => ({ ...l, zoom: true }));
      await integrationApi.zoomAuth();
      const recs = await integrationApi.zoomRecordings();
      setZoomRecordings(recs || []);
      toast.success('Connected to Zoom successfully');
    } catch (e) {
      toast.error(e.displayMessage || 'Zoom connection failed');
    } finally {
      setLoading(l => ({ ...l, zoom: false }));
    }
  };

  const handleConnectGoogle = async () => {
    try {
      setLoading(l => ({ ...l, google: true }));
      await integrationApi.googleAuth();
      const recs = await integrationApi.googleRecordings();
      setGoogleRecordings(recs || []);
      toast.success('Connected to Google Meet successfully');
    } catch (e) {
      toast.error(e.displayMessage || 'Google connection failed');
    } finally {
      setLoading(l => ({ ...l, google: false }));
    }
  };

  const handleImport = async (id, provider) => {
    try {
      setImporting(prev => ({ ...prev, [id]: true }));
      if (provider === 'zoom') {
        await integrationApi.zoomImport(id);
      } else {
        await integrationApi.googleImport(id);
      }
      toast.success('Recording import started. Check Meetings page for progress.');
    } catch (e) {
      toast.error(e.displayMessage || 'Import failed');
    } finally {
      setImporting(prev => ({ ...prev, [id]: false }));
    }
  };

  return (
    <>
      <div className="page-head">
        <div className="page-head-row">
          <div>
            <h1>Integrations</h1>
            <p>Connect your external meeting platforms to import recordings directly.</p>
          </div>
        </div>
      </div>

      <div className="stack stack-lg">
        {/* Zoom Integration */}
        <Card title="Zoom" hint="Import cloud recordings from your Zoom account">
          <div className="row" style={{ marginBottom: 16 }}>
            {zoomRecordings !== null ? (
              <Badge tone="success">Connected</Badge>
            ) : (
              <Button icon={Plug} onClick={handleConnectZoom} loading={loading.zoom}>
                Connect Zoom
              </Button>
            )}
          </div>
          
          {zoomRecordings && (
            <div className="stack stack-sm">
              <h3>Recent Recordings</h3>
              {zoomRecordings.length === 0 ? (
                <p className="text-muted">No cloud recordings found.</p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Title</th>
                        <th>Duration</th>
                        <th>Date</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {zoomRecordings.map(rec => (
                        <tr key={rec.id}>
                          <td>
                            <div className="row" style={{ gap: 8 }}><Video size={16} />{rec.topic}</div>
                          </td>
                          <td className="mono text-muted">{formatDuration(rec.duration * 60)}</td>
                          <td className="text-muted">{formatDate(rec.start_time)}</td>
                          <td>
                            <Button 
                              size="sm" 
                              icon={Download} 
                              onClick={() => handleImport(rec.id, 'zoom')}
                              loading={importing[rec.id]}
                            >
                              Import
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>

        {/* Google Meet Integration */}
        <Card title="Google Meet" hint="Import recordings from your Google Drive">
          <div className="row" style={{ marginBottom: 16 }}>
            {googleRecordings !== null ? (
              <Badge tone="success">Connected</Badge>
            ) : (
              <Button icon={Plug} onClick={handleConnectGoogle} loading={loading.google}>
                Connect Google Meet
              </Button>
            )}
          </div>
          
          {googleRecordings && (
            <div className="stack stack-sm">
              <h3>Recent Recordings</h3>
              {googleRecordings.length === 0 ? (
                <p className="text-muted">No recordings found in Google Drive.</p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Title</th>
                        <th>Date</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {googleRecordings.map(rec => (
                        <tr key={rec.id}>
                          <td>
                            <div className="row" style={{ gap: 8 }}><Video size={16} />{rec.name}</div>
                          </td>
                          <td className="text-muted">{formatDate(rec.createdTime)}</td>
                          <td>
                            <Button 
                              size="sm" 
                              icon={Download} 
                              onClick={() => handleImport(rec.id, 'google')}
                              loading={importing[rec.id]}
                            >
                              Import
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
