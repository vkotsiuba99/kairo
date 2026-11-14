{{- define "kairo.labels" -}}
app.kubernetes.io/part-of: kairo
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Оточення бекенду: спільне з ConfigMap, секрети з Secret, плюс роль і БД сервісу */}}
{{- define "kairo.backendEnv" -}}
envFrom:
  - configMapRef:
      name: {{ .root.Release.Name }}-config
  - secretRef:
      name: {{ .root.Release.Name }}-secrets
env:
  - name: SERVICE
    value: {{ .service | quote }}
  - name: POSTGRES_DB
    value: {{ .spec.database | default "" | quote }}
  {{- range $key, $value := .spec.env }}
  - name: {{ $key }}
    value: {{ $value | quote }}
  {{- end }}
{{- end }}

{{/* Том медіа: Media пише, Publishing читає */}}
{{- define "kairo.mediaMount" -}}
{{- if .spec.media }}
volumeMounts:
  - name: media
    mountPath: /app/data/media
    readOnly: {{ eq .spec.media "readOnly" }}
{{- end }}
{{- end }}

{{- define "kairo.mediaVolume" -}}
{{- if .spec.media }}
volumes:
  - name: media
    persistentVolumeClaim:
      claimName: {{ .root.Release.Name }}-media
{{- end }}
{{- end }}
