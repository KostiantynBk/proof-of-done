import type {Run, Sandbox, SummaryItem} from './types';
export function mergeSandboxes(existing:Sandbox[], incoming:Sandbox[]):Sandbox[]{
 const byId=new Map(existing.map(s=>[s.sandbox_id,s]));
 incoming.forEach(s=>byId.set(s.sandbox_id,s));
 return [...byId.values()];
}
export function survival(items:SummaryItem[],origin:'targeted'|'blind'):string{
 const survived=items.reduce((n,item)=>n+(item.survived_by_origin?.[origin]??0),0);
 if(items.length>0&&items.every(item=>typeof item.mutations_by_origin?.[origin]==='number')){
  const total=items.reduce((n,item)=>n+(item.mutations_by_origin?.[origin]??0),0);return `${survived}/${total}`;
 }
 return String(survived);
}
export function elapsed(run:Run, now:number, finishedEvent?:string):string{
 const start=Date.parse(run.created_at); if(!Number.isFinite(start))return 'Unavailable';
 const terminal=['done','failed'].includes(run.status);
 const finish=Date.parse(run.finished_at??run.ended_at??finishedEvent??'');
 const end=Number.isFinite(finish)?finish:terminal?NaN:now;
 if(!Number.isFinite(end))return 'Unavailable';
 const sec=Math.max(0,Math.floor((end-start)/1000));return `${Math.floor(sec/60)}m ${String(sec%60).padStart(2,'0')}s`;
}
