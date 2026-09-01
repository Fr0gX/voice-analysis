import type {AsrConfig,Result,Task} from './types'
const detail=async(r:Response)=>{if(r.ok)return r;let m=`请求失败 (${r.status})`;try{const j=await r.json();m=j.detail?.message||m}catch{}throw new Error(m)}
export async function createTask(audio:File,mode:'provided_transcript'|'cloud_asr',segments?:string,config?:AsrConfig){const f=new FormData();f.append('audio',audio);f.append('input_mode',mode);if(mode==='provided_transcript'){f.append('segments',new Blob([segments||''],{type:'application/json'}),'segments.json')}else if(config){f.append('asr_provider',config.provider);f.append('asr_credentials',JSON.stringify(config.credentials));f.append('asr_options',JSON.stringify(config.options))}return (await detail(await fetch('/v1/tasks',{method:'POST',body:f}))).json() as Promise<Task>}
export async function getTask(id:string){return (await detail(await fetch(`/v1/tasks/${id}`))).json() as Promise<Task>}
export async function getResult(id:string){return (await detail(await fetch(`/v1/tasks/${id}/result`))).json() as Promise<Result>}
export async function cancelTask(id:string){return (await detail(await fetch(`/v1/tasks/${id}/cancel`,{method:'POST'}))).json() as Promise<Task>}
export async function deleteTask(id:string){await detail(await fetch(`/v1/tasks/${id}`,{method:'DELETE'}))}
export async function validateAsr(config:AsrConfig){const f=new FormData();f.append('asr_provider',config.provider);f.append('asr_credentials',JSON.stringify(config.credentials));f.append('asr_options',JSON.stringify(config.options));await detail(await fetch('/v1/asr/validate',{method:'POST',body:f}))}
export const exportUrl=(id:string,format:string)=>`/v1/tasks/${id}/exports/${format}`
