import {describe,it,expect,vi,afterEach} from 'vitest';
import {elapsed,mergeSandboxes,survival} from './view-model';
import {run,sandboxes} from '../mocks';
import {api,ApiError} from './client';
afterEach(()=>vi.unstubAllGlobals());
describe('run view model',()=>{
 it('merges incoming sandbox events and deduplicates by id',()=>{const updated={...sandboxes[0],stdout_tail:'updated'};const result=mergeSandboxes([sandboxes[0]],[updated,sandboxes[1]]);expect(result).toHaveLength(2);expect(result[0].stdout_tail).toBe('updated')});
 it('shows per-origin denominators only when all are present',()=>{expect(survival(run.items,'blind')).toBe('3/20');const partial=run.items.map(item=>({...item,mutations_by_origin:undefined}));expect(survival(partial,'blind')).toBe('3')});
 it('freezes elapsed at finish and avoids inventing unknown terminal times',()=>{const start='2026-11-05T10:00:00Z';const r={...run,created_at:start,finished_at:undefined,status:'done' as const};expect(elapsed(r,Date.parse(start)+90000,'2026-11-05T10:01:05Z')).toBe('1m 05s');expect(elapsed(r,Date.now())).toBe('Unavailable');expect(elapsed({...r,status:'running'},Date.parse(start)+90000)).toBe('1m 30s')});
});
describe('recorded-run fallback',()=>{
 it('treats a 404 as no recording',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('',{status:404})));expect(await api.recorded()).toBeNull()});
 it('preserves the recorded run id in a 429 response',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'budget exhausted',recorded_run_id:'rec-123'}),{status:429})));try{await api.start();throw Error('expected failure')}catch(e){expect(e).toBeInstanceOf(ApiError);expect((e as ApiError).recordedRunId).toBe('rec-123')}});
});
