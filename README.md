# 🤖 Agente Iterativo de Generación de Código con DeepSeek

## Descripción
Agente autónomo que genera código Python iterativamente con auto-evaluación.

## Instalación
El bundle ya está instalado en `/Applications/AgenteDeepSeek`

## Configuración Rápida
```bash
# Configurar API key
agente-deepseek configurar

# O editar directamente
nano /Applications/AgenteDeepSeek/.env
```

## Uso
```bash
# Ejecutar con configuración por defecto
agente-deepseek

# Guardar resultado
agente-deepseek --output solucion.py

# Ver detalles
agente-deepseek --verbose

# Ayuda
agente-deepseek --help
```

## Personalización
Edita `/Applications/AgenteDeepSeek/config/objetivos.json`
