# EU Agri Tracker

Seguimiento automático de los expedientes legislativos de la UE en los que intervienen las comisiones
**AGRI, PECH y REGI** del Parlamento Europeo (como responsables o con opinión), con sus pasos en el Consejo.
Se actualiza solo en GitHub, sin servidor y sin coste.

**Web:** `https://<tu-usuario>.github.io/eu-agri-tracker/`

## Qué hace cada día

1. **Parlamento Europeo** (Open Data Portal): lee los expedientes que han cambiado y vuelve a leer su ficha:
   fase, comisiones, ponente y ponentes alternativos, pasos y documentos. También el calendario de plenos y
   qué expedientes están en el orden del día.
2. **Consejo** (canales RSS y registro de documentos): reuniones AGRIFISH, grupos de trabajo, Coreper y
   Comité Especial de Agricultura, órdenes del día y documentos de agricultura y pesca. Lee las primeras
   páginas de cada documento nuevo para saber a qué expediente se refiere.
3. **Compara** con el día anterior y apunta los cambios.
4. **Avisa** de los cambios en los expedientes que sigues (ver abajo).
5. **Publica** la web.

Se ejecuta de lunes a viernes a las 05:41 y a las 14:41 (hora de Madrid) y el sábado por la mañana.
Puedes lanzarlo a mano en **Actions → Update tracker → Run workflow**.

## Seguir un expediente y recibir avisos

- En la ficha del expediente, pulsa **Seguir este expediente**. Se abre un *issue* de GitHub ya rellenado:
  pulsa **Create** (o **Submit new issue**).
- Cada cambio se publica como comentario en ese issue y **GitHub te lo envía por correo**.
- Para dejar de seguirlo, **cierra el issue**.
- También puedes poner números de expediente en `follow:` dentro de `config.yml`.

Los correos van a la dirección de notificaciones de tu cuenta de GitHub
(Settings → Notifications → Default notifications email). Puedes poner ahí el correo del trabajo.

## Ajustes (`config.yml`)

Se editan en GitHub con el lápiz y **Commit changes**:

- `committees`, `roles`: qué comisiones y papeles cuentan.
- `procedure_types`, `since_year`: tipos de procedimiento y desde qué año.
- `also_track`: expedientes que quieres aunque no cumplan la regla.
- `follow`: expedientes de los que quieres avisos.
- `legislative_train`: enlaces al Legislative Train, puestos a mano.
- `notifications.daily_digest`: `true` para recibir además un resumen diario con todos los cambios.

## Limitaciones conocidas

- La web principal del Parlamento (Legislative Train, páginas de comisiones), las páginas de EUR-Lex y las
  páginas web del Consejo bloquean la lectura automática. Los enlaces funcionan con normalidad para ti;
  solo el robot no puede leerlas.
- Los pasos intermedios del Consejo (orientación general, mandato) se detectan por los documentos y las
  notas de prensa que citan el expediente; pueden escaparse algunos.
- Las votaciones en comisión no se publican en datos abiertos.

## Estructura

- `tracker/`: el programa (Python). `run.py` lo ejecuta todo.
- `site/`: la web (HTML, CSS y JavaScript, sin dependencias).
- `data/`: los datos guardados en cada ejecución (historial en el propio repositorio).
- `.github/workflows/update.yml`: la programación diaria.

Datos: Parlamento Europeo, Consejo de la UE y Oficina de Publicaciones de la UE (reutilización permitida
citando la fuente). Proyecto personal, no oficial.
