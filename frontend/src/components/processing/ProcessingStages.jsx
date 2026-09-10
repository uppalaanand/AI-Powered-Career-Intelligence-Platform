import { AlertCircle, Check, Circle, Loader2, MinusCircle } from 'lucide-react';
import { STAGE_STATE } from '../../utils/constants';

const ICONS = {
  [STAGE_STATE.DONE]: { Icon: Check, color: 'var(--success)' },
  [STAGE_STATE.ACTIVE]: { Icon: Loader2, color: 'var(--accent)', spin: true },
  [STAGE_STATE.FAILED]: { Icon: AlertCircle, color: 'var(--danger)' },
  [STAGE_STATE.SKIPPED]: { Icon: MinusCircle, color: 'var(--ink-faint)' },
  [STAGE_STATE.PENDING]: { Icon: Circle, color: 'var(--line-strong)' },
};

/**
 * The processing screen's stage list. Each row reflects a real backend result:
 * a stage is only ticked once the call that performs it has returned.
 */
export function ProcessingStages({ stages }) {
  return (
    <ol className="stages">
      {stages.map((stage) => {
        const { Icon, color, spin } = ICONS[stage.state] || ICONS[STAGE_STATE.PENDING];
        return (
          <li key={stage.id} className={`stage is-${stage.state}`}>
            <span className="stage-icon" style={{ color }}>
              <Icon size={16} className={spin ? 'spin' : undefined} aria-hidden="true" />
            </span>
            <div>
              <div className="stage-name">{stage.name}</div>
              <div className="stage-detail">{stage.detail}</div>
            </div>
            <span className="visually-hidden">{stateLabel(stage.state)}</span>
          </li>
        );
      })}
    </ol>
  );
}

function stateLabel(state) {
  switch (state) {
    case STAGE_STATE.DONE:
      return 'completed';
    case STAGE_STATE.ACTIVE:
      return 'in progress';
    case STAGE_STATE.FAILED:
      return 'failed';
    case STAGE_STATE.SKIPPED:
      return 'skipped';
    default:
      return 'not started';
  }
}
