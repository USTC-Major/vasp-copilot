// ============================================================
// DiagnosisUploadPage — 上传已有计算目录 zip
// ============================================================

import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Alert } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';
import DiagnosisUploadPanel from '../components/diagnosis/DiagnosisUploadPanel';
import ErrorAlert from '../components/common/ErrorAlert';
import { useDiagnosisRun } from '../hooks/useApi';
import type { DetectedRun } from '../types/generated-api';
import '../components/diagnosis/scientific-diagnosis.css';

const DiagnosisUploadPage: React.FC = () => {
  const navigate = useNavigate();
  const [diagnosisId, setDiagnosisId] = useState<string | null>(null);
  const [detected, setDetected] = useState<DetectedRun | null>(null);

  const runMutation = useDiagnosisRun();

  const handleDetected = useCallback((id: string, detectedRun: DetectedRun) => {
    setDiagnosisId(id);
    setDetected(detectedRun);
  }, []);

  const handleStartDiagnosis = useCallback(async () => {
    if (!diagnosisId) return;
    try {
      await runMutation.mutateAsync(diagnosisId);
      navigate(`/diagnosis/${diagnosisId}`);
    } catch {
      // handled by error display
    }
  }, [diagnosisId, runMutation, navigate]);

  return (
    <div className="diagnosis-page diagnosis-upload-page">
      <header className="diagnosis-heading">
        <h1>诊断计算</h1>
        <p>上传已有计算目录，检查输出文件、问题与诊断证据。</p>
      </header>

      <DiagnosisUploadPanel onDetected={handleDetected} />

      {detected && (
        <div className="diagnosis-start">
          <Alert
            type="success"
            showIcon
            message="文件检测完成，点击开始诊断"
            style={{ marginBottom: 16 }}
          />
          <Button
            type="primary"
            size="large"
            icon={<PlayCircleOutlined />}
            onClick={handleStartDiagnosis}
            loading={runMutation.isPending}
          >
            开始诊断
          </Button>
        </div>
      )}

      {runMutation.error && (
        <div style={{ marginTop: 16 }}>
          <ErrorAlert error={runMutation.error} onRetry={() => runMutation.reset()} />
        </div>
      )}
    </div>
  );
};

export default DiagnosisUploadPage;
