const titleKey=s=>s.title.toLocaleLowerCase().trim().replace(s.year?new RegExp(`\\s*\\(${s.year}\\)$`):/$^/,'');
// Join missing-year entries only when the same title has exactly one known year.
// Distinct remakes and individually named special events remain separate.
export function groupMovies(rows){
 const years=new Map();
 for(const s of rows){const title=titleKey(s);if(!years.has(title))years.set(title,new Set());if(s.year)years.get(title).add(s.year);}
 const groups=new Map();
 for(const s of rows){const title=titleKey(s),known=years.get(title);const year=s.year||(known.size===1?[...known][0]:'');const key=`${title}|${year}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(s);}
 return groups;
}
