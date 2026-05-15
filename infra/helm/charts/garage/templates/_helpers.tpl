{{- define "garage.fullname" -}}
{{- .Release.Name }}
{{- end }}

{{- define "garage.labels" -}}
app.kubernetes.io/name: garage
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "garage.selectorLabels" -}}
app.kubernetes.io/name: garage
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
