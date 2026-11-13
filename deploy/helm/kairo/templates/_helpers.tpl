{{- define "kairo.labels" -}}
app.kubernetes.io/part-of: kairo
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Спільне оточення бекенду: несекретне з ConfigMap, секрети — з Secret */}}
{{- define "kairo.backendEnv" -}}
envFrom:
  - configMapRef:
      name: {{ .Release.Name }}-config
  - secretRef:
      name: {{ .Release.Name }}-secrets
{{- end }}
