export type Provider='tencent'|'aliyun'
export type AsrConfig={provider:Provider;credentials:Record<string,string>;options:Record<string,string>}
export type Task={task_id:string;status:string;stage?:string;result_status?:string;input_mode:string;asr_provider?:Provider;transcript_source?:Record<string,string>;error?:{code:string;message:string;stage?:string}}
export type Segment={id:string;start_ms:number;end_ms:number;text:string;speaker?:string|number;confidence?:number;assignment?:{label:string;confidence?:number;reason?:string;risk?:{level?:string}};source?:Record<string,unknown>}
export type Result={status:'success'|'partial';segments:Segment[];speakers:Record<string,unknown>[];warnings?:Record<string,unknown>[];transcript_source?:Record<string,string>}
