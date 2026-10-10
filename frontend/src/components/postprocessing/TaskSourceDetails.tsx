import { Typography } from 'antd';
import type { PPTaskSource } from '../../api/postprocessing';

export default function TaskSourceDetails({ source }: { source: PPTaskSource }) {
  return <div className="pp-task-source">
    <Typography.Text strong>任务结果 · {source.cached_at ? '本地缓存副本' : '待取回'}</Typography.Text>
    <dl className="pp-source-metadata">
      <dt>项目／任务</dt><dd>{source.project_id} ／ {source.task_id}</dd>
      <dt>计算作业</dt><dd>{source.job_key} · 调度作业号 {source.slurm_id}</dd>
      <dt>执行尝试</dt><dd>{source.attempt_id}</dd>
      <dt>远端目录</dt><dd>{source.remote_directory}</dd>
      {source.cached_at && <><dt>缓存时间</dt><dd>{source.cached_at}</dd></>}
    </dl>
    <Typography.Paragraph type="secondary">作业终态只用于允许取回；科学收敛仍以结果解析为准。远端目录中现有文件不保证由本次计算产生。</Typography.Paragraph>
  </div>;
}
