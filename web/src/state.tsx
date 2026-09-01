import {createContext,useContext,useState,type ReactNode} from 'react'
import type {AsrConfig} from './types'
type State={asr?:AsrConfig;setAsr:(v:AsrConfig|undefined)=>void}
const Context=createContext<State|undefined>(undefined)
export function AppState({children}:{children:ReactNode}){const[asr,setAsr]=useState<AsrConfig>();return <Context.Provider value={{asr,setAsr}}>{children}</Context.Provider>}
export function useAppState(){const v=useContext(Context);if(!v)throw new Error('missing state');return v}
export function rememberTask(id:string){const ids=JSON.parse(sessionStorage.getItem('voice-analysis-task-ids')||'[]') as string[];sessionStorage.setItem('voice-analysis-task-ids',JSON.stringify([id,...ids.filter(x=>x!==id)].slice(0,20)))}
