# SERCOP Dashboard — Guía de despliegue en Railway

## ¿Qué hace?
- Scrapea automáticamente el catálogo electrónico de SERCOP cada 4 horas
- Guarda órdenes pendientes y ganadores en una base de datos
- Muestra un dashboard web en tiempo real accesible desde cualquier navegador
- Se actualiza solo: sin tocar nada, los datos se refrescan solos

---

## Paso 1 — Crear cuenta en Railway
1. Ir a https://railway.app
2. Registrarse con GitHub (gratis)

---

## Paso 2 — Subir el código a GitHub
1. Crear un repositorio en https://github.com/new (puede ser privado)
2. Subir todos estos archivos:
   - main.py
   - requirements.txt
   - Procfile
   - nixpacks.toml
   - static/index.html

Comandos si tienes Git instalado:
```bash
git init
git add .
git commit -m "SERCOP dashboard inicial"
git remote add origin https://github.com/TU_USUARIO/sercop-dashboard.git
git push -u origin main
```

---

## Paso 3 — Crear proyecto en Railway
1. Entrar a https://railway.app/dashboard
2. Click en "New Project" → "Deploy from GitHub repo"
3. Seleccionar tu repositorio
4. Railway detecta automáticamente el Procfile y nixpacks.toml

---

## Paso 4 — Variables de entorno (IMPORTANTE)
En Railway → tu servicio → "Variables", agregar:

| Variable         | Valor                    |
|------------------|--------------------------|
| SERCOP_RUC       | 1000973329001            |
| SERCOP_USUARIO   | CARLINADAVILA            |
| SERCOP_CLAVE     | Cdavila973329*           |
| INTERVAL_H       | 4                        |
| PORT             | 8080                     |

---

## Paso 5 — Generar dominio público
1. En Railway → tu servicio → "Settings" → "Networking"
2. Click "Generate Domain"
3. Comparte ese link con tu equipo

---

## Uso del dashboard
- **Auto-refresh**: la página se refresca automáticamente cada 4 horas
- **Sincronizar ahora**: botón en la barra superior para forzar una actualización
- **Filtros**: buscar por entidad, código CE, categoría o estado
- **Tabla**: muestra el proveedor ganador cuando ya está asignado

---

## Estructura del proyecto
```
sercop-dashboard/
├── main.py          ← Bot + API Flask
├── requirements.txt
├── Procfile
├── nixpacks.toml    ← Instala Chrome en Railway
├── static/
│   └── index.html   ← Dashboard web
└── sercop.db        ← Base de datos (se crea automático)
```

---

## Solución de problemas
- **Login falla**: verificar credenciales en variables de entorno
- **Chrome no inicia**: Railway instala chromium vía nixpacks, no modificar
- **No aparecen ganadores**: la URL `/asignadas` puede variar; revisar el DOM del sitio real

---

## Contacto
pauldavila@vagadamia.com
