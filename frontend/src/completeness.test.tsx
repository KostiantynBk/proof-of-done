import {render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {cleanup} from '@testing-library/react';
import {CompletenessPanel, ExportNotice, GuardAssessment, MutationCaveat} from './components';

afterEach(cleanup);

describe('honest completeness', () => {
  it('shows clean results without a guard failure assertion', () => {
    render(<CompletenessPanel data={{component_failures: 0, skipped_gaps: [], guard_decisions: []}}/>);
    expect(screen.getByText(/Component failure entries: 0/)).toBeTruthy();
    expect(screen.queryByText(/Generated guard failed/)).toBeNull();
  });

  it('shows skipped work, rejected guard, and measured failure entries', () => {
    render(<CompletenessPanel data={{component_failures: 1,
      skipped_gaps: ['REQ-01', 'REQ-02', 'REQ-03', 'REQ-04'].map(item_id => ({item_id, reason: 'six item limit'})),
      guard_decisions: [{item_id: 'RULE-01', decision: 'rejected', original: 'failed'}]}}/>);
    expect(screen.getByText(/Component failure entries: 1.*Skipped gap attempts: 4/)).toBeTruthy();
    expect(screen.getByText('REQ-04: six item limit')).toBeTruthy();
    expect(screen.getByText(/RULE-01: rejected/)).toBeTruthy();
    expect(screen.getByText(/may be a test defect, reviewer assessment required/)).toBeTruthy();
    expect(screen.queryByText(/confirmed violation/i)).toBeNull();
  });

  it('distinguishes saved downloads from the separate run-specific supplement', () => {
    render(<ExportNotice/>);
    expect(screen.getByText(/Downloads are preserved snapshots/)).toBeTruthy();
    expect(screen.getByText(/applies only to the run identified there/)).toBeTruthy();
    expect(screen.getByRole('link', {name: 'Recorded-demo review supplement'}).getAttribute('href'))
      .toBe('https://github.com/KostiantynBk/proof-of-done/blob/main/docs/results/tuned-run-review.md');
    expect(screen.queryByText(/refreshed/i)).toBeNull();
  });

  it('distinguishes review from confirmation and explains mutation limits', () => {
    render(<><GuardAssessment failed approved={false}/><MutationCaveat/></>);
    expect(screen.getByText('Approval: Rejected')).toBeTruthy();
    expect(screen.getByText(/may be a test defect/)).toBeTruthy();
    expect(screen.getByText(/Equivalent or off-target edits can survive/)).toBeTruthy();
  });
});
