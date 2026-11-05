import {describe,it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {OutcomeChip,VerdictChip} from './components';
import {replaySummary} from './api/client';
import {run} from './mocks';
describe('evidence display',()=>{it('maps each verdict to its semantic colour',()=>{const {container}=render(<VerdictChip value="VIOLATED"/>);expect(container.querySelector('.rose')?.textContent).toContain('VIOLATED')});it('renders the sandbox outcome as text',()=>{render(<OutcomeChip value="timeout_killed"/>);expect(screen.getByText('timeout killed')).toBeTruthy()});it('reveals replay results only after the verdict stage',()=>{const early=replaySummary(run,0);expect(early.items).toHaveLength(0);expect(early.stages[0].status).toBe('running');const complete=replaySummary(run,65000);expect(complete.status).toBe('done');expect(complete.items).toHaveLength(12)})});
