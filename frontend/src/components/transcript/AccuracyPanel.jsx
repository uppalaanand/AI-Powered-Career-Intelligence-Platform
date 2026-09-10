import { useState } from 'react';
import { CheckCircle2, Gauge, AlertTriangle } from 'lucide-react';
import { Alert, Button, Card } from '../ui';
import { accuracyApi } from '../../services/api';
import { DEFAULT_TARGET_ACCURACY } from '../../utils/constants';
import { percent } from '../../utils/format';

/**
 * Transcription accuracy testing (Milestone 1).
 *
 * The user pastes a reference transcript; the backend aligns it with the stored
 * transcript and returns Word Error Rate plus the individual missing, incorrect
 * and extra words. Every number shown here is calculated, never assumed.
 */
export function AccuracyPanel({ meetingId }) {
  const [reference, setReference] = useState('');
  const [target, setTarget] = useState(DEFAULT_TARGET_ACCURACY);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const compare = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await accuracyApi.compare({
        referenceTranscript: reference,
        meetingId,
        targetAccuracy: Number(target) || DEFAULT_TARGET_ACCURACY,
      });
      setResult(data);
    } catch (caught) {
      setError(caught.displayMessage || 'The comparison could not be completed.');
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card
      title="Check transcription accuracy"
      hint="Paste a correct transcript of the same recording to measure how close Whisper got."
    >
      <div className="field">
        <label className="label" htmlFor="reference-transcript">
          Reference transcript
        </label>
        <textarea
          id="reference-transcript"
          className="textarea"
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          placeholder="Paste the correct, human-written transcript of this recording here."
        />
        <p className="help-text">
          Casing, punctuation and filler words such as &quot;um&quot; are ignored, so only real
          wording differences count as errors.
        </p>
      </div>

      <div className="row-between" style={{ marginBottom: 16 }}>
        <div className="row">
          <label className="label" htmlFor="target-accuracy" style={{ marginBottom: 0 }}>
            Target accuracy
          </label>
          <input
            id="target-accuracy"
            className="input"
            type="number"
            min={0}
            max={100}
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            style={{ width: 90 }}
          />
          <span className="text-muted text-sm">%</span>
        </div>
        <Button
          variant="primary"
          icon={Gauge}
          onClick={compare}
          loading={loading}
          disabled={!reference.trim()}
        >
          Compare transcripts
        </Button>
      </div>

      {error && <Alert tone="danger" title="Comparison failed">{error}</Alert>}

      {result && <AccuracyResult result={result} />}
    </Card>
  );
}

function AccuracyResult({ result }) {
  const passed = result.passed;
  return (
    <div className="accuracy-result" style={{ marginTop: 18 }}>
      <div className={`accuracy-score ${passed ? 'is-pass' : 'is-fail'}`}>
        <div className="accuracy-value">{percent(result.accuracy_percentage)}</div>
        <div className="row" style={{ marginTop: 10, gap: 6 }}>
          {passed ? (
            <CheckCircle2 size={15} style={{ color: 'var(--success)' }} aria-hidden="true" />
          ) : (
            <AlertTriangle size={15} style={{ color: 'var(--warn)' }} aria-hidden="true" />
          )}
          <strong>{result.status}</strong>
        </div>
        <div className="accuracy-caption">
          Target {percent(result.target_accuracy, 0)} &middot; Word Error Rate{' '}
          {percent(result.word_error_rate)}
        </div>
      </div>

      <div className="stack stack-md">
        <div className="metric-grid">
          <Metric value={result.reference_word_count} label="Reference words" />
          <Metric value={result.hypothesis_word_count} label="Transcript words" />
          <Metric value={result.correct_words} label="Correct" />
          <Metric value={result.substitutions} label="Incorrect" />
          <Metric value={result.deletions} label="Missing" />
          <Metric value={result.insertions} label="Extra" />
        </div>

        <p className="text-sm text-muted" style={{ margin: 0 }}>
          {result.explanation}
        </p>

        <WordGroup
          title="Missing words"
          description="In the reference transcript, but not recognised."
          words={result.missing_words}
          className="is-missing"
        />

        <WordGroup
          title="Extra words"
          description="Recognised, but never said in the reference."
          words={result.extra_words}
          className="is-extra"
        />

        {result.incorrect_words?.length > 0 && (
          <div>
            <div className="text-sm" style={{ fontWeight: 600 }}>
              Incorrect words
            </div>
            <div className="text-xs text-muted">Heard as something else.</div>
            <div className="word-chip-group">
              {result.incorrect_words.slice(0, 60).map((diff, index) => (
                <span className="word-chip is-wrong" key={`${diff.reference_word}-${index}`}>
                  {diff.reference_word} &rarr; {diff.hypothesis_word}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({ value, label }) {
  return (
    <div className="metric">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

function WordGroup({ title, description, words, className }) {
  if (!words?.length) return null;
  return (
    <div>
      <div className="text-sm" style={{ fontWeight: 600 }}>
        {title}
      </div>
      <div className="text-xs text-muted">{description}</div>
      <div className="word-chip-group">
        {words.slice(0, 60).map((word, index) => (
          <span className={`word-chip ${className}`} key={`${word}-${index}`}>
            {word}
          </span>
        ))}
        {words.length > 60 && <span className="word-chip">+{words.length - 60} more</span>}
      </div>
    </div>
  );
}
