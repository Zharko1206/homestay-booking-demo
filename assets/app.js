/* Vue 3 application. Every write goes through the local API. */
const { createApp, ref, computed, onMounted, nextTick } = Vue;
createApp({
  setup() {
    const c = PROJECT;
    const allowedViews = ['overview', 'records', 'submissions', 'modules', 'audit'];
    const view = ref(allowedViews.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
    const records = ref(structuredClone(c.records));
    const items = ref(structuredClone(c.items));
    const audits = ref([]), submissions = ref([]), online = ref(false), ready = ref(false), busy = ref(false);
    const query = ref(''), status = ref(''), category = ref('全部'), page = ref(1);
    const modal = ref(''), editingId = ref(''), formCells = ref([]), formStatus = ref(''), formError = ref('');
    const selectedItem = ref(null), orderName = ref(''), quantity = ref(1), orderNote = ref('');
    const localDate = d => [d.getFullYear(), String(d.getMonth()+1).padStart(2,'0'), String(d.getDate()).padStart(2,'0')].join('-');
    const today = localDate(new Date());
    const nextDate = value => { const d = new Date((value || today)+'T12:00:00'); d.setDate(d.getDate()+1); return localDate(d); };
    const startDate = ref(today), endDate = ref(nextDate(today)), runResult = ref(null), toast = ref(''), dialog = ref(null);
    let toastTimer, previousFocus;
    function notify(message) { toast.value = message; clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.value = '', 4500); }
    async function api(path, options = {}) {
      if (location.protocol === 'file:') throw Error('请先启动后端，在 http://127.0.0.1:8000 访问页面后保存');
      let response;
      try { response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers }, signal: AbortSignal.timeout(8000) }); }
      catch { throw Error('无法连接本地数据服务，请检查后端是否启动'); }
      const data = await response.json().catch(() => ({error:'当前地址未提供后端 API，请运行 python3 backend/server.py'}));
      if (!response.ok) throw Error(data.error || '请求失败');
      return data;
    }
    async function refresh() {
      busy.value = true;
      try {
        await api('/api/health');
        const [r, i, a, s] = await Promise.all([api('/api/records?page_size=100'), api('/api/items'), api('/api/audit'), api('/api/submissions')]);
        // Traverse additional pages so client-side filtering and metrics use the whole dataset.
        const all = [...r.items];
        for (let n=2; all.length<r.total; n++) all.push(...(await api('/api/records?page_size=100&page='+n)).items);
        records.value = all; items.value = i.items; audits.value = a.items; submissions.value = s.items; online.value = true;
      } catch (error) { online.value = false; if (ready.value) notify(error.message); }
      finally { busy.value = false; ready.value = true; }
    }
    const navigation = computed(() => [
      {key:'overview', label:c.consumer?'项目首页':'业务总览'},
      c.consumer?{key:'submissions',label:c.submissionTitle}:{key:'records',label:c.recordNav},
      {key:'modules',label:'模块设计'}, {key:'audit',label:'操作日志'}
    ]);
    const viewTitle = computed(() => navigation.value.find(n=>n.key===view.value)?.label || c.recordTitle);
    function go(key) { view.value=key; query.value=''; status.value=''; page.value=1; location.hash=key; window.scrollTo({top:0,behavior:'instant'}); }
    window.addEventListener('hashchange', () => { if(allowedViews.includes(location.hash.slice(1))) view.value=location.hash.slice(1); });
    async function openModule(i) { go('modules'); await nextTick(); document.getElementById('module-'+i)?.scrollIntoView({block:'center',behavior:'smooth'}); }
    const label = key => c.statuses[key] || key;
    const distribution = computed(() => Object.entries(c.statuses).map(([key, label]) => ({key,label,count:records.value.filter(r=>r.status===key).length})));
    const attention = computed(() => records.value.filter(r=>['warn','bad','fail','firing'].includes(r.status)));
    const metrics = computed(() => [
      {label:c.layout==='testing'?'用例记录':c.layout==='monitor'?'事件记录':'业务记录',value:records.value.length,unit:'条',note:'当前演示数据'},
      {label:c.statuses.ok || c.statuses.pass || c.statuses.resolved || '已处理',value:records.value.filter(r=>['ok','pass','resolved'].includes(r.status)).length,unit:'条',note:'按业务状态汇总'},
      {label:'待跟进记录',value:attention.value.length,unit:'条',note:'需人工确认'},
      {label:'已记录操作',value:audits.value.length,unit:'次',note:'最近 100 条操作日志'}
    ]);
    const categories = computed(() => ['全部', ...new Set(items.value.map(x=>x.cat))]);
    const filteredItems = computed(() => items.value.filter(x => (category.value==='全部'||x.cat===category.value) && (x.title+' '+x.place+' '+x.cat+' '+x.time).toLowerCase().includes(query.value.toLowerCase())));
    const filteredRecords = computed(() => records.value.filter(r=>(!status.value||r.status===status.value) && r.cells.join(' ').toLowerCase().includes(query.value.toLowerCase())));
    const pageItems = computed(() => filteredItems.value.slice((page.value-1)*6,page.value*6));
    const pageRecords = computed(() => filteredRecords.value.slice((page.value-1)*8,page.value*8));
    function requireOnline() { if(!online.value) throw Error('当前为静态预览。请启动 python3 backend/server.py 后从本地服务访问，业务提交才会保存。'); }
    async function showModal(kind) { previousFocus = document.activeElement; modal.value=kind; formError.value=''; document.body.style.overflow='hidden'; await nextTick(); dialog.value?.focus(); }
    function closeModal() { if(busy.value)return; modal.value=''; document.body.style.overflow=''; previousFocus?.focus(); }
    function editRecord(r) { editingId.value=r?.id||''; formCells.value=r?[...r.cells]:c.columns.map(()=>''); formStatus.value=r?.status||Object.keys(c.statuses)[0]; showModal('record'); }
    function openOrder(item) { selectedItem.value=item; orderName.value=''; quantity.value=1; orderNote.value=''; showModal('order'); }
    async function saveRecord() {
      formError.value='';
      try { requireOnline(); busy.value=true; await api('/api/records'+(editingId.value?'/'+editingId.value:''), {method:editingId.value?'PATCH':'POST',body:JSON.stringify({cells:formCells.value,status:formStatus.value})}); await refresh(); busy.value=false; closeModal(); notify('记录已保存到数据库'); }
      catch(e) { formError.value=e.message; } finally { busy.value=false; }
    }
    async function deleteRecord() {
      formError.value='';
      try { requireOnline(); if(!confirm('确定删除这条业务记录？此操作无法撤销。'))return; busy.value=true; await api('/api/records/'+editingId.value,{method:'DELETE'}); await refresh(); page.value=1; busy.value=false; closeModal(); notify('记录已删除'); }
      catch(e) { formError.value=e.message; } finally {busy.value=false;}
    }
    async function submitOrder() {
      formError.value='';
      try { requireOnline(); busy.value=true; const saved=await api('/api/submissions',{method:'POST',body:JSON.stringify({item_id:selectedItem.value.id,name:orderName.value,quantity:quantity.value,note:orderNote.value,start_date:startDate.value,end_date:endDate.value})}); await refresh(); busy.value=false; closeModal(); notify('登记成功 · '+saved.id); }
      catch(e) { formError.value=e.message; } finally {busy.value=false;}
    }
    async function runCheck() { try { requireOnline(); busy.value=true; runResult.value=await api('/api/run',{method:'POST',body:'{}'}); await refresh(); notify('本地自检完成，结果已归档'); } catch(e) {notify(e.message);} finally {busy.value=false;} }
    function exportCsv() {
      const safe = value => { const s=String(value); return '"'+(/^[=+@\-\t\r]/.test(s)?"'":'')+s.replaceAll('"','""')+'"'; };
      const csv='\ufeff'+[c.columns.concat('状态'),...filteredRecords.value.map(r=>[...r.cells,label(r.status)])].map(row=>row.map(safe).join(',')).join('\r\n');
      const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})); const a=document.createElement('a');a.href=url;a.download=c.short+'-业务记录.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('已导出当前筛选结果');
    }
    const formatTime = value => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '';
    // Keep keyboard focus inside an open dialog.
    window.addEventListener('keydown', event => {
      if(!modal.value)return;
      if(event.key==='Escape')closeModal();
      if(event.key!=='Tab')return;
      const list=dialog.value?.querySelectorAll('button:not([disabled]),input,select,textarea'); if(!list?.length)return;
      const first=list[0],last=list[list.length-1];
      if(event.shiftKey && (document.activeElement===first||document.activeElement===dialog.value)){event.preventDefault();last.focus();}
      else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
    });
    onMounted(refresh);
    return { c,view,records,items,audits,submissions,online,ready,busy,query,status,category,page,modal,editingId,formCells,formStatus,formError,selectedItem,orderName,quantity,orderNote,today,startDate,endDate,nextDate,runResult,toast,dialog,navigation,viewTitle,label,distribution,attention,metrics,categories,filteredItems,filteredRecords,pageItems,pageRecords,go,openModule,refresh,editRecord,openOrder,closeModal,saveRecord,deleteRecord,submitOrder,runCheck,exportCsv,formatTime };
  }
}).mount('#app');
