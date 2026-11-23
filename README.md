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
   Los expedientes que esos documentos citan y que el Parlamento no lleva en AGRI, PECH o REGI también entran:
   del Parlamento si los tiene, y si no como **expedientes «solo Consejo»** (posibilidades de pesca, acuerdos
   de pesca, posiciones de la UE en el Consejo Oleícola, etc.: actos que adopta solo el Consejo).
3. **Compara** con el día anterior y apunta los cambios.
4. **Avisa** de los cambios en los expedientes que sigues (ver abajo).
5. **Publica** la web.

Se ejecuta de lunes a viernes a las 05:41, 10:11, 14:41 y 19:11 (hora de Madrid en invierno) y los fines de
semana por la mañana. GitHub puede retrasar o saltarse alguna ejecución programada; la siguiente la cubre.
Puedes lanzarlo a mano en **Actions → Update tracker → Run workflow**.

## Qué verás en la web

- **Ficha de cada expediente**, con un recuadro «Dónde está»: las etapas del procedimiento (propuesta,
  comisión del PE, posición del PE, posición del Consejo, trílogos, acuerdo, aprobaciones y Diario Oficial),
  cuál es la actual, qué viene después y la próxima fecha anunciada. Las etapas del Consejo se deducen de sus
  documentos (mandatos, orientaciones generales, textos de compromiso), así que alguna puede aparecer con retraso.
- **Tablero**: los expedientes legislativos en curso, en la columna de la etapa en la que están.
- **Pleno**: cada sesión plenaria desde julio de 2024 (y las ya convocadas), día a día, con horarios, debates y
  votaciones, su resultado (a favor–en contra–abstenciones) y los puntos de AGRI, PECH, REGI, de los expedientes
  del tracker y de sus temas marcados en color. Se abre en la sesión en curso o la próxima. Antes de diciembre
  de 2024 el Parlamento no publica el orden del día en datos abiertos: para esas sesiones salen los debates del
  acta y las votaciones.
- **Etiquetas** (PAC, MFF 2028-2034, Pesca…) para filtrar y buscar; se definen en `config.yml`.
- **Cambios** con tres niveles: importante, normal y trámite menor.

## Seguir un expediente y recibir avisos

- En la ficha del expediente, pulsa **Seguir este expediente**. Se abre un *issue* de GitHub ya rellenado:
  pulsa **Create** (o **Submit new issue**).
- Los cambios se publican como comentario en ese issue y **GitHub te los envía por correo**: lo importante
  (votaciones, trílogos, mandatos, acuerdos, fechas anunciadas) en cuanto se detecta; los trámites menores
  (documentos sueltos, enmiendas presentadas) se acumulan y llegan en un resumen semanal, o junto con la
  siguiente novedad importante.
- Para dejar de seguirlo, **cierra el issue**.
- También puedes poner números de expediente en `follow:` dentro de `config.yml`.

Los correos van a la dirección de notificaciones de tu cuenta de GitHub
(Settings → Notifications → Default notifications email). Puedes poner ahí el correo del trabajo.

## Ajustes (`config.yml`)

Se editan en GitHub con el lápiz y **Commit changes**:

- `committees`, `roles`: qué comisiones y papeles cuentan.
- `procedure_types`, `since_year`: tipos de procedimiento y desde qué año.
- `also_track`: expedientes que quieres aunque no cumplan la regla (también los que solo están en el Consejo,
  p. ej. `2026/0269(NLE)`).
- `follow`: expedientes de los que quieres avisos.
- `tags` y `tag_words`: tus etiquetas, por número de expediente o por palabras del título.
- `legislative_train`: enlaces al Legislative Train, puestos a mano.
- `notifications.daily_digest`: `true` para recibir además un resumen diario con todos los cambios.

## Limitaciones conocidas

- La web principal del Parlamento (Legislative Train, páginas de comisiones), las páginas de EUR-Lex y las
  páginas web del Consejo bloquean la lectura automática. Los enlaces funcionan con normalidad para ti;
  solo el robot no puede leerlas.
- Los pasos intermedios del Consejo (orientación general, mandato) se detectan por los documentos y las
  notas de prensa que citan el expediente; pueden escaparse algunos.
- Las votaciones en comisión no se publican en datos abiertos.
- Un expediente «solo Consejo» aparece cuando un documento público de agricultura o pesca del Consejo cita su
  número; muchos documentos de trabajo son de difusión limitada y no se pueden leer.
- No cubre los actos de ejecución de la Comisión (comitología), las consultas previas a la propuesta
  (*Have your say*) ni los expedientes anteriores a `since_year`.

## Estructura

- `tracker/`: el programa (Python). `run.py` lo ejecuta todo.
- `site/`: la web (HTML, CSS y JavaScript, sin dependencias).
- `data/`: los datos guardados en cada ejecución (historial en el propio repositorio).
- `.github/workflows/update.yml`: la programación diaria.

Datos: Parlamento Europeo, Consejo de la UE y Oficina de Publicaciones de la UE (reutilización permitida
citando la fuente). Proyecto personal, no oficial.
