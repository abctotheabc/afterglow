export function minutes(time){const [h,m]=time.split(':').map(Number);return h*60+m;}
export function clock(n){const day=Math.floor(n/1440);n=((n%1440)+1440)%1440;const h=Math.floor(n/60);return `${h%12||12}:${String(n%60).padStart(2,'0')} ${h<12?'AM':'PM'}${day>0?' (+1 day)':''}`;}
export function estimate(time,runtime,min=15,max=20,credits=5){
 const start=minutes(time);const valid=Number.isFinite(runtime)&&runtime>0;
 return {start:[start+min,start+max],end:valid?[start+min+runtime,start+max+runtime]:null,credits:valid?[start+min+Math.max(0,runtime-credits),start+max+Math.max(0,runtime-credits)]:null};
}
export function range(v){return v?(v[0]===v[1]?clock(v[0]):`${clock(v[0])} – ${clock(v[1])}`):'Runtime unavailable';}
export function easternDate(now=new Date()){return new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);}
export function matches(s,f){
 if(s.area!==f.area||s.date!==f.date)return false;
 if(f.source&&s.source!==f.source)return false;
 if(f.chain&&(s.chain||'Independent / other')!==f.chain)return false;
 if(f.theater&&s.theater!==f.theater)return false;
 if(f.query&&!`${s.title} ${s.theater} ${s.director||''}`.toLowerCase().includes(f.query.toLowerCase()))return false;
 if(f.after&&minutes(s.time)<minutes(f.after))return false;
 if(f.before){const e=estimate(s.time,s.runtime,f.min,f.max,f.credits);if(!e.end||e.end[1]>minutes(f.before))return false;}
 if(f.remaining&&s.date===easternDate()){const n=new Intl.DateTimeFormat('en-GB',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date());if(minutes(s.time)<minutes(n))return false;}
 return true;
}
