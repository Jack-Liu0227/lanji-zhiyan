/* Selection identities differ from per-project grants and team runtime members. */
const ExecutorChoices=(()=>{
  const target=row=>row.target_id||row.assistant_id||row.team_id||row.id;
  const identity=row=>row.kind+':'+target(row);
  const matches=(row,id)=>row.id===id||id==='global:'+identity(row);
  const find=(rows,id)=>(rows||[]).find(row=>row.id===id)||(rows||[]).find(row=>matches(row,id));
  const ready=row=>row.enabled!==false&&row.enabled!==0&&row.available!==false&&!row.disabled_reason;
  function unique(rows,preferredId=''){
    const groups=new Map();
    function rank(row){return [matches(row,preferredId)?0:1,row.kind==='team'&&row.owner_project_id&&row.owner_project_id===row.project_id?0:1,row.id.startsWith('global:')?1:0,ready(row)?0:1];}
    function before(left,right){const a=rank(left),b=rank(right);for(let i=0;i<a.length;i++)if(a[i]!==b[i])return a[i]<b[i];return String(left.created_at||'').localeCompare(String(right.created_at||''))<0;}
    for(const row of rows||[]){if(!row?.id||!row.kind)continue;const key=row.selection_key||identity(row),old=groups.get(key);if(!old||before(row,old))groups.set(key,row);}
    const result=[...groups.values()].map(row=>({...row})),counts=new Map();
    const name=row=>row.name||row.assistant_name||row.team_name||target(row);
    for(const row of result){const key=row.kind+':'+name(row).normalize('NFKC').trim().toLocaleLowerCase();counts.set(key,(counts.get(key)||0)+1);}
    const labels=new Map();
    for(const row of result){
      const key=row.kind+':'+name(row).normalize('NFKC').trim().toLocaleLowerCase();
      let label=name(row);
      if(counts.get(key)>1)label+=' · '+(row.selection_key?.startsWith('team-preset:')?'内置团队':row.owner_project_title||String(target(row)).slice(-6));
      row.display_name=label;const labelKey=row.kind+':'+label;labels.set(labelKey,(labels.get(labelKey)||0)+1);
    }
    for(const row of result)if(labels.get(row.kind+':'+row.display_name)>1)row.display_name+=' · '+String(target(row)).slice(-8);
    const finalLabels=new Map();for(const row of result){const key=row.kind+':'+row.display_name;finalLabels.set(key,(finalLabels.get(key)||0)+1);}
    for(const row of result)if(finalLabels.get(row.kind+':'+row.display_name)>1)row.display_name+=' · '+String(target(row));
    return result.sort((a,b)=>(a.kind==='team'?0:1)-(b.kind==='team'?0:1)||a.display_name.localeCompare(b.display_name,'zh-CN'));
  }
  function members(rows,preferredId=''){
    const groups=new Map();
    for(const row of rows||[]){
      if(!row.assistant_id||!['leader','member','teammate','executor'].includes(row.role)||['disabled','archived'].includes(row.status))continue;
      const old=groups.get(row.assistant_id);
      if(!old||row.id===preferredId||(old.id!==preferredId&&row.role==='leader'))groups.set(row.assistant_id,row);
    }
    return [...groups.values()];
  }
  return {unique,members,find,ready,matches};
})();
