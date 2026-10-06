import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Upload, Waves, Search, Filter, ArrowLeft, ArrowRight } from 'lucide-react';
import { Alert, Button, Card, EmptyState, Spinner, Badge } from '../components/ui';
import { MeetingsTable } from '../components/meetings/MeetingsTable';
import { useMeetings } from '../hooks/useMeetings';
import { knowledgeApi } from '../services/api';
import { STATUS } from '../utils/constants';
import { formatDate } from '../utils/format';

export function MeetingsPage() {
  const [page, setPage] = useState(0);
  const limit = 10;
  
  const [statusFilter, setStatusFilter] = useState('');
  const [sortField, setSortField] = useState('created_at');
  const [sortOrder, setSortOrder] = useState('desc');
  
  const { meetings, total, loading, error } = useMeetings({ status: statusFilter, limit, offset: page * limit });

  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState(null);
  const [searchError, setSearchError] = useState(null);

  const handleSearch = async (e) => {
    e?.preventDefault();
    const query = searchQuery.trim();
    if (!query) {
      setSearchResults(null);
      return;
    }
    
    setIsSearching(true);
    setSearchError(null);
    try {
      const res = await knowledgeApi.search(query);
      setSearchResults(res.results || []);
    } catch (err) {
      setSearchError(err);
    } finally {
      setIsSearching(false);
    }
  };

  // Local sorting and date range (simple implementation since API might not support date range sorting natively)
  const sortedMeetings = [...meetings].sort((a, b) => {
    let aVal = a[sortField];
    let bVal = b[sortField];
    if (sortField === 'created_at') {
      aVal = new Date(a.created_at).getTime();
      bVal = new Date(b.created_at).getTime();
    }
    if (aVal < bVal) return sortOrder === 'asc' ? -1 : 1;
    if (aVal > bVal) return sortOrder === 'asc' ? 1 : -1;
    return 0;
  });

  return (
    <>
      <div className="page-head">
        <div className="page-head-row">
          <div>
            <h1>Meetings</h1>
            <p>Every recording you have uploaded, newest first.</p>
          </div>
          <Link to="/upload">
            <Button variant="primary" icon={Upload}>
              Upload meeting
            </Button>
          </Link>
        </div>
      </div>

      <Card>
        <div className="row" style={{ gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
          <form onSubmit={handleSearch} className="row" style={{ flex: 1, minWidth: 250 }}>
            <input 
              className="input" 
              placeholder="Search meetings by meaning..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{ flex: 1 }}
            />
            <Button type="submit" icon={Search} loading={isSearching}>Search</Button>
            {searchResults && (
              <Button type="button" onClick={() => { setSearchQuery(''); setSearchResults(null); }}>Clear</Button>
            )}
          </form>

          <div className="row">
            <Filter size={16} className="text-muted" />
            <select 
              className="input" 
              value={statusFilter} 
              onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
            >
              <option value="">All Statuses</option>
              {Object.keys(STATUS).map(s => (
                <option key={s} value={STATUS[s]}>{STATUS[s]}</option>
              ))}
            </select>
          </div>

          <div className="row">
            <select 
              className="input" 
              value={`${sortField}-${sortOrder}`} 
              onChange={(e) => {
                const [f, o] = e.target.value.split('-');
                setSortField(f);
                setSortOrder(o);
              }}
            >
              <option value="created_at-desc">Date (Newest first)</option>
              <option value="created_at-asc">Date (Oldest first)</option>
              <option value="title-asc">Title (A-Z)</option>
              <option value="title-desc">Title (Z-A)</option>
            </select>
          </div>
        </div>

        {searchError && (
          <Alert tone="danger" title="Search failed">
            {searchError.displayMessage || 'Unable to search meeting knowledge.'}
          </Alert>
        )}

        {searchResults ? (
          <div className="stack stack-sm">
            <h3>Search Results</h3>
            {searchResults.length === 0 ? (
              <p className="text-muted">No meetings matched your search.</p>
            ) : (
              searchResults.map(result => (
                <Card key={result.meeting_id} title={result.meeting_title} actions={<Badge tone="neutral">{Math.round(result.score * 100)}% match</Badge>}>
                  <div className="stack stack-sm">
                    <div className="row">
                      <span className="text-xs text-muted">{formatDate(result.meeting_date)}</span>
                    </div>
                    <p className="summary-text">{result.excerpt}</p>
                    <Link to={`/meetings/${result.meeting_id}`}>Open meeting</Link>
                  </div>
                </Card>
              ))
            )}
          </div>
        ) : (
          <>
            {error && (
              <Alert tone="danger" title="Could not load meetings">
                {error.displayMessage || 'The backend did not respond.'}
              </Alert>
            )}

            {loading && <Spinner label="Loading meetings" />}

            {!loading && !error && (
              <>
                {sortedMeetings.length ? (
                  <MeetingsTable meetings={sortedMeetings} />
                ) : (
                  <EmptyState
                    icon={Waves}
                    title="Nothing here yet"
                    action={
                      <Link to="/upload">
                        <Button variant="primary" icon={Upload}>
                          Upload a recording
                        </Button>
                      </Link>
                    }
                  >
                    Uploaded meetings and their transcripts will appear here.
                  </EmptyState>
                )}
                
                {total > limit && (
                  <div className="row" style={{ justifyContent: 'space-between', marginTop: 16 }}>
                    <span className="text-sm text-muted">Showing {page * limit + 1} to {Math.min((page + 1) * limit, total)} of {total}</span>
                    <div className="row">
                      <Button size="sm" icon={ArrowLeft} disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>Previous</Button>
                      <Button size="sm" icon={ArrowRight} disabled={(page + 1) * limit >= total} onClick={() => setPage(p => p + 1)}>Next</Button>
                    </div>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </Card>
    </>
  );
}
