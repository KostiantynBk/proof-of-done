import type {Health,ItemDetail,Preset,Run,Sandbox} from './types';
import * as mock from '../mocks';
export const isMock=import.meta.env.VITE_MOCK==='1';
let started=0; const approvals:Record<string,boolean>={};
export class ApiError extends Error { constructor(public status:number, message:string, public recordedRunId?:string){super(message);} }
async function request<T>(path:string, init?:RequestInit):Promise<T>{
 const response=await fetch(`/api${path}`,init);
 if(!response.ok){let msg=`Request failed (${response.status})`;let recordedRunId:string|undefined;
  try{const data=await response.json();const detail=data.detail;msg=typeof detail==='string'?detail:typeof detail?.message==='string'?detail.message:msg;const id=data.recorded_run_id??detail?.recorded_run_id;if(typeof id==='string'&&id)recordedRunId=id;}catch{/* not JSON */}
  throw new ApiError(response.status,response.status===429?`Rate limit reached. ${msg}`:msg,recordedRunId);
 }
 if(response.status===204)return undefined as T;return response.json() as Promise<T>;
}
const post=(body:unknown):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export function replaySummary(base:Run, elapsed:number):Run {const step=Math.min(13,Math.floor(elapsed/4600));if(step>=13)return {...base,id:'mock-live'};const fraction=step/13;return {...base,id:'mock-live',status:'running',stage:`S${step}`,stages:base.stages.map((s,i)=>({...s,status:i<step?'done':i===step?'running':'pending'})),items:step>=10?base.items:[],counts:step>=10?base.counts:{PROVEN:0,WEAK:0,UNTESTED:0,VIOLATED:0,UNVERIFIED:0,INCONCLUSIVE:0},ai_covered:step>=6?base.ai_covered:0,containment:{...base.containment,sandboxes:Math.floor(base.containment.sandboxes*fraction),destroyed:Math.floor(base.containment.destroyed*fraction),telemetry:step>=4?base.containment.telemetry:{}},exports:[]};}
export const api={
 health:()=>isMock?Promise.resolve(mock.health):request<Health>('/health'),
 runs:()=>isMock?Promise.resolve([mock.run]):request<Run[]>('/runs'),
 recorded:()=>isMock?Promise.resolve({run_id:'demo-recorded'}):request<{run_id:string}>('/runs/recorded').catch(e=>{if(e instanceof ApiError&&e.status===404)return null;throw e;}),
 start:()=>{if(isMock){started=Date.now();sessionStorage.setItem('pod-mock-start',String(started));return Promise.resolve({run_id:'mock-live'});}return request<{run_id:string}>('/runs',post({project:'demo:refundly'}));},
 run:(id:string)=>isMock?Promise.resolve(id==='mock-live'?replaySummary(mock.run,Date.now()-(started||Number(sessionStorage.getItem('pod-mock-start'))||Date.now())):mock.run):request<Run>(`/runs/${encodeURIComponent(id)}`),
 item:(id:string,item:string)=>{if(isMock){const result=mock.detail(item);if(result.gap&&item in approvals)result.gap.result.approved=approvals[item];return Promise.resolve(result);}return request<ItemDetail>(`/runs/${encodeURIComponent(id)}/items/${encodeURIComponent(item)}`);},
 sandboxes:()=>isMock?Promise.resolve(mock.sandboxes):request<Sandbox[]>('/sandboxes?limit=50'),
 presets:()=>isMock?Promise.resolve(mock.presets):request<Preset[]>('/playground/presets'),
 playground:(code:string)=>isMock?Promise.resolve({...((code.includes('while True')||code.includes('os.fork'))?mock.sandboxes[43]:mock.sandboxes[0]),run_id:null,purpose:'playground',telemetry:code.includes('.aws/credentials')?mock.sandboxes[0].telemetry:[],stdout_tail:'Synthetic mock result — code was not executed.'}):request<Sandbox|{sandbox_run:Sandbox}>('/playground',post({code})).then(result=>'sandbox_run' in result?result.sandbox_run:result),
 approve:(id:string,item:string,approve:boolean,reviewer:string)=>{if(isMock){approvals[item]=approve;return Promise.resolve();}return request<void>(`/runs/${encodeURIComponent(id)}/approvals/${encodeURIComponent(item)}`,post({approve,reviewer}));},
 exportUrl:(id:string,name:string)=>`/api/runs/${encodeURIComponent(id)}/export/${encodeURIComponent(name)}`,
};
