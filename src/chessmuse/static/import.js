// Poll real server work; the progress bar animates between actual completed stages.
export async function prepareGame(payload, report, fetcher = fetch,
                                  wait = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  report(0, "上传并校验棋谱");
  const response = await fetcher("api/import", {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload),
  });
  const accepted = await response.json();
  if (!response.ok) throw new Error(accepted.error || "棋谱导入失败");
  for (;;) {
    const poll = await fetcher(`api/jobs/${accepted.job_id}`, {cache:"no-store"});
    const job = await poll.json();
    if (!poll.ok) throw new Error(job.error || "无法获取导入进度，请重新导入");
    report(job.progress, job.message);
    if (job.status === "error") throw new Error(job.message);
    if (job.status === "complete") return job.result;
    await wait(450);
  }
}
