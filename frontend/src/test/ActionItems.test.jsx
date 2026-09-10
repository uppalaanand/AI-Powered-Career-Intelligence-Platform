import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ActionItemsTable } from '../components/intelligence/ActionItemsTable';
import { ParticipantList } from '../components/intelligence/ParticipantList';

const items = [
  {
    id: '1',
    task: 'Complete API integration',
    assigned_to: 'Ravi',
    deadline: 'Friday',
    priority: 'high',
    status: 'pending',
  },
  {
    id: '2',
    task: 'Prepare UI testing report',
    assigned_to: null,
    deadline: null,
    priority: 'medium',
    status: 'pending',
  },
];

describe('ActionItemsTable', () => {
  it('renders every action item with its fields', () => {
    render(<ActionItemsTable items={items} />);
    expect(screen.getByText('Complete API integration')).toBeInTheDocument();
    expect(screen.getByText('Ravi')).toBeInTheDocument();
    expect(screen.getByText('Friday')).toBeInTheDocument();
    expect(screen.getByText('High')).toBeInTheDocument();
  });

  it('shows missing owners and deadlines as "Not stated" rather than inventing them', () => {
    render(<ActionItemsTable items={items} />);
    expect(screen.getAllByText('Not stated')).toHaveLength(2);
  });

  it('shows an empty state when nothing was agreed', () => {
    render(<ActionItemsTable items={[]} />);
    expect(screen.getByText('No action items')).toBeInTheDocument();
  });
});

describe('ParticipantList', () => {
  it('renders participants with initials and merged aliases', () => {
    render(
      <ParticipantList
        participants={[
          { id: '1', name: 'Ravi Kumar', aliases: ['Ravi'], is_unknown: false, mention_count: 3 },
        ]}
      />
    );
    expect(screen.getByText('Ravi Kumar')).toBeInTheDocument();
    expect(screen.getByText('RK')).toBeInTheDocument();
    expect(screen.getByText(/also: Ravi/)).toBeInTheDocument();
  });

  it('handles the no-participants case', () => {
    render(<ParticipantList participants={[]} />);
    expect(screen.getByText('No participants identified')).toBeInTheDocument();
  });
});
