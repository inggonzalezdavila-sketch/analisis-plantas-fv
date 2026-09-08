# Análisis de Plantas FV

MVP local para analizar exportaciones XLSX de inversores fotovoltaicos. Consolida plantas, estima generación desde el contador acumulado, muestra producción diaria y prioriza alertas operativas.

## Ejecutar

Haga doble clic en `Iniciar_Monitor_FV.bat`. Se abrirá la aplicación en el navegador con la dirección correcta: `http://127.0.0.1:8000`.

También puede iniciarla en PowerShell, desde esta carpeta:

```powershell
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' app.py
```

Abra `http://127.0.0.1:8000` y use **Cargar archivos XLSX** para seleccionar el período que desea analizar. La carga sustituye el lote anterior, así que si necesita comparar dos plantas debe seleccionar ambos archivos en la misma operación. **Limpiar datos** elimina el lote activo y deja el tablero vacío.

## Alcance del primer MVP

- Lectura de exportaciones con dimensión interna incorrecta (un caso habitual en reportes de fabricantes).
- Producción estimada por diferencia del contador acumulado, planta e inversor.
- Curva diaria comparativa, huecos de telemetría y alertas de temperatura o desbalance de potencia.
- Acciones sugeridas para cada alerta.

La conexión directa a la plataforma de la planta será una siguiente fase. Requerirá confirmar fabricante/plataforma, método de autenticación y autorización de acceso.

## Publicar en internet

La aplicación se puede publicar como servicio web con Render y un repositorio privado en GitHub. No use GitHub Pages: esta aplicación ejecuta Python y recibe archivos XLSX.

1. Cree un repositorio privado vacío en GitHub, por ejemplo `analisis-plantas-fv`.
2. Suba este proyecto al repositorio. El archivo `.gitignore` evita que se suban los reportes XLSX y la configuración local.
3. En Render, cree un servicio web y conecte el repositorio. Render detectará `render.yaml`.
4. Tras el primer despliegue, Render entregará una URL pública `https://...onrender.com`.

El almacenamiento de archivos de un plan básico puede ser temporal. Para operación real, agregue inicio de sesión y almacenamiento persistente antes de cargar reportes de clientes o de producción.

## Seguridad y usuarios

En Render, la autenticación está activada por defecto. Antes del primer despliegue protegido, cree estas variables en **Environment** como secretos; no las escriba en GitHub ni las comparta:

- `AUTH_REQUIRED`: `true`
- `APP_SESSION_SECRET`: una cadena aleatoria larga (mínimo 32 caracteres).
- `APP_USERS_JSON`: usuarios y roles permitidos. Ejemplo de estructura, sustituyendo los valores de ejemplo por contraseñas propias:

```json
{"admin":{"password":"cambia-esta-contraseña-larga","role":"admin"},"tecnico":{"password":"otra-contraseña-larga","role":"technician"}}
```

Roles disponibles: `admin` puede consultar, cargar y limpiar datos; `technician` puede consultar y cargar; `viewer` solo puede consultar. Cambiar `APP_SESSION_SECRET` cierra todas las sesiones activas. La integración futura con fabricantes deberá usar credenciales de API de solo lectura almacenadas también como secretos de Render.
