# -*- coding: utf-8 -*-
import base64
import logging

from lxml import etree

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Claves de sesión HTTP que identifican al técnico en el dispositivo.
SESSION_EMPLOYEE = 'visar_field_employee_id'
SESSION_SHIFT = 'visar_field_session_id'

# Estados de tarea considerados "cerrados" (no se muestran como pendientes).
CLOSED_STATES = ('1_done', '1_canceled')

# Campo de enlace de la worksheet dinámica hacia la tarea (res_model = project.task).
WORKSHEET_LINK = 'x_project_task_id'
# Campos de la worksheet que NO se editan en el formulario (igual que el reporte nativo).
WORKSHEET_OMIT = {WORKSHEET_LINK, 'x_name'}
# Tipos relacionales/complejos que el formulario de campo v1 no edita.
WORKSHEET_SKIP_TYPES = ('one2many', 'many2many')
# Reporte nativo de la worksheet FSM.
FSM_REPORT = 'industry_fsm.worksheet_custom'


class VisarFieldApp(http.Controller):
    """App de campo para técnicos (patrón POS): un dispositivo, identificación por PIN,
    sin usuario interno. Escribe en la worksheet NATIVA y campos de firma nativos para
    que los reportes nativos de Field Service funcionen sin cambios.

    Todas las rutas son públicas y operan en sudo, pero acotadas estrictamente al
    empleado identificado en la sesión del dispositivo.
    """

    # ==================================================================
    # Helpers de sesión / técnico
    # ==================================================================
    def _current_employee(self):
        emp_id = request.session.get(SESSION_EMPLOYEE)
        if not emp_id:
            return request.env['hr.employee'].sudo().browse()
        return request.env['hr.employee'].sudo().browse(emp_id).exists()

    def _employee_tasks(self, employee, include_closed=False):
        domain = [('visar_technician_ids', 'in', employee.ids)]
        if not include_closed:
            domain.append(('state', 'not in', list(CLOSED_STATES)))
        return request.env['project.task'].sudo().search(
            domain, order='planned_date_begin asc, priority desc, id desc')

    def _task_for_employee(self, task_id, employee):
        task = request.env['project.task'].sudo().browse(int(task_id)).exists()
        if task and employee and employee in task.visar_technician_ids:
            return task
        return request.env['project.task'].sudo().browse()

    # ==================================================================
    # Worksheet nativa (modelo dinámico x_...)
    # ==================================================================
    def _worksheet_model(self, task):
        """Recordset sudo del modelo dinámico de la worksheet de la tarea, o None."""
        template = task.worksheet_template_id
        if not template or not template.sudo().model_id:
            return None
        model_name = template.sudo().model_id.model
        if model_name not in request.env:
            return None
        return request.env[model_name].sudo()

    def _worksheet_record(self, task, create=False):
        """Registro worksheet de la tarea (uno por tarea); lo crea si hace falta."""
        Model = self._worksheet_model(task)
        if Model is None:
            return None
        record = Model.search(
            [(WORKSHEET_LINK, '=', task.id)], limit=1, order='create_date desc')
        if not record and create:
            record = Model.create({WORKSHEET_LINK: task.id})
        return record

    def _worksheet_field_names(self, Model):
        """Nombres de campos editables, en el orden del formulario nativo."""
        names = []
        try:
            view = Model.get_view(view_type='form')
            arch = etree.fromstring(view['arch'])
            for node in arch.iter('field'):
                name = node.get('name')
                if not name or name in WORKSHEET_OMIT or name in names:
                    continue
                if node.get('invisible') in ('1', 'True', 'true'):
                    continue
                names.append(name)
        except Exception:  # noqa: BLE001 - vista dinámica; degradar a fields_get
            _logger.warning("Worksheet form view ilegible para %s", Model._name)
        if not names:
            names = [
                n for n in Model.fields_get()
                if n.startswith('x_') and n not in WORKSHEET_OMIT
            ]
        return names

    def _worksheet_field_descriptors(self, task, record):
        """Lista ordenada de dicts para renderizar el formulario de la worksheet."""
        Model = self._worksheet_model(task)
        if Model is None:
            return []
        meta = Model.fields_get()
        descriptors = []
        for name in self._worksheet_field_names(Model):
            info = meta.get(name)
            if not info or info.get('type') in WORKSHEET_SKIP_TYPES:
                continue
            ftype = info['type']
            desc = {
                'name': name,
                'type': ftype,
                'string': info.get('string') or name,
                'help': info.get('help') or '',
                'selection': info.get('selection') or [],
                'required': bool(info.get('required')),
                'value': record[name] if record else False,
                'options': [],
                'value_id': False,
                'has_file': False,
            }
            if ftype == 'many2one' and info.get('relation'):
                comodel = request.env[info['relation']].sudo()
                desc['options'] = comodel.search_read([], ['display_name'], limit=200)
                desc['value_id'] = record[name].id if (record and record[name]) else False
            elif ftype == 'binary':
                desc['has_file'] = bool(record and record[name])
            descriptors.append(desc)
        return descriptors

    def _worksheet_write_values(self, task, record, post, files):
        """Coacciona y devuelve los valores a escribir en la worksheet."""
        Model = self._worksheet_model(task)
        if Model is None:
            return {}
        meta = Model.fields_get()
        allowed = {
            d['name']: d['type']
            for d in self._worksheet_field_descriptors(task, record)
        }
        vals = {}
        for name, ftype in allowed.items():
            if ftype == 'boolean':
                vals[name] = bool(post.get(name))
            elif ftype == 'binary':
                upload = files.get(name)
                if upload:
                    data = upload.read()
                    if data:
                        vals[name] = base64.b64encode(data)
            elif name not in post:
                continue
            elif ftype == 'integer':
                vals[name] = int(post.get(name) or 0)
            elif ftype in ('float', 'monetary'):
                vals[name] = float(post.get(name) or 0)
            elif ftype == 'many2one':
                vals[name] = int(post[name]) if post.get(name) else False
            else:  # char, text, html, selection, date, datetime
                vals[name] = post.get(name) or False
        # No permitir tocar el enlace ni el nombre.
        for protected in WORKSHEET_OMIT:
            vals.pop(protected, None)
        return vals

    # ==================================================================
    # Login por PIN
    # ==================================================================
    @http.route('/visar/field', type='http', auth='public', website=True, sitemap=False)
    def field_login(self, **kw):
        if self._current_employee():
            return request.redirect('/visar/field/tasks')
        return request.render('visar_field_app.field_login', {'error': kw.get('error')})

    @http.route('/visar/field/login', type='http', auth='public', website=True,
                methods=['POST'], csrf=True)
    def field_login_submit(self, **post):
        employee = request.env['hr.employee']._visar_field_find_by_pin(post.get('pin'))
        if not employee:
            return request.redirect('/visar/field?error=1')

        shift = request.env['visar.field.session'].sudo().create({
            'employee_id': employee.id,
            'note': request.httprequest.user_agent.string[:120]
            if request.httprequest.user_agent else False,
        })
        request.session[SESSION_EMPLOYEE] = employee.id
        request.session[SESSION_SHIFT] = shift.id
        return request.redirect('/visar/field/tasks')

    @http.route('/visar/field/logout', type='http', auth='public', website=True,
                methods=['POST'], csrf=True)
    def field_logout(self, **post):
        shift_id = request.session.get(SESSION_SHIFT)
        if shift_id:
            shift = request.env['visar.field.session'].sudo().browse(shift_id).exists()
            if shift and shift.state == 'open':
                shift.action_close()
        request.session.pop(SESSION_EMPLOYEE, None)
        request.session.pop(SESSION_SHIFT, None)
        return request.redirect('/visar/field')

    # ==================================================================
    # Lista de servicios del técnico
    # ==================================================================
    @http.route('/visar/field/tasks', type='http', auth='public', website=True,
                sitemap=False)
    def field_tasks(self, **kw):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        return request.render('visar_field_app.field_tasks', {
            'employee': employee,
            'tasks': self._employee_tasks(employee),
        })

    # ==================================================================
    # Detalle de un servicio
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>', type='http', auth='public',
                website=True, sitemap=False)
    def field_task_detail(self, task_id, **kw):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        photos = request.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'project.task'),
            ('res_id', '=', task.id),
            ('mimetype', 'like', 'image/'),
        ], order='id desc')

        worksheet = self._worksheet_record(task)
        return request.render('visar_field_app.field_task_detail', {
            'employee': employee,
            'task': task,
            'photos': photos,
            'has_worksheet_template': bool(task.worksheet_template_id),
            'worksheet_fields': self._worksheet_field_descriptors(task, worksheet),
            'is_signed': bool(task.worksheet_signature),
            'saved': kw.get('saved'),
        })

    # ==================================================================
    # Captura: fotos (adjuntos en la tarea)
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/photo', type='http', auth='public',
                website=True, methods=['POST'], csrf=True)
    def field_task_photo(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        Attachment = request.env['ir.attachment'].sudo()
        for upload in request.httprequest.files.getlist('photos'):
            data = upload.read()
            if not data:
                continue
            Attachment.create({
                'name': upload.filename or 'foto.jpg',
                'datas': base64.b64encode(data),
                'res_model': 'project.task',
                'res_id': task.id,
                'mimetype': upload.mimetype or 'image/jpeg',
            })
        return request.redirect('/visar/field/task/%s' % task.id)

    # Sirve una imagen (adjunto de la tarea) acotada al técnico de la sesión.
    @http.route('/visar/field/task/<int:task_id>/image/<int:attachment_id>',
                type='http', auth='public', website=True, sitemap=False)
    def field_task_image(self, task_id, attachment_id, **kw):
        employee = self._current_employee()
        task = self._task_for_employee(task_id, employee) if employee else None
        if not task:
            return request.not_found()
        attachment = request.env['ir.attachment'].sudo().browse(attachment_id).exists()
        if (not attachment or attachment.res_model != 'project.task'
                or attachment.res_id != task.id):
            return request.not_found()
        return request.make_response(
            base64.b64decode(attachment.datas or b''),
            [('Content-Type', attachment.mimetype or 'image/jpeg')])

    # ==================================================================
    # Captura: worksheet nativa
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/worksheet', type='http', auth='public',
                website=True, methods=['POST'], csrf=True)
    def field_task_worksheet(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        record = self._worksheet_record(task, create=True)
        if record is not None:
            vals = self._worksheet_write_values(
                task, record, post, request.httprequest.files)
            if vals:
                record.write(vals)
        return request.redirect('/visar/field/task/%s?saved=1' % task.id)

    # ==================================================================
    # Cierre del servicio (firma nativa + atribución)
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/close', type='http', auth='public',
                website=True, methods=['POST'], csrf=True)
    def field_task_close(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        vals = {
            'visar_field_closed_by_id': employee.id,
            'visar_field_closed_at': fields.Datetime.now(),
            'state': '1_done',
        }
        # Firma del cliente en los campos NATIVOS (los usa el reporte nativo).
        signature = self._decode_data_url(post.get('signature'))
        if signature:
            vals['worksheet_signature'] = signature
            vals['worksheet_signed_by'] = post.get('signature_name') or False
        task.write(vals)
        return request.redirect('/visar/field/task/%s?saved=1' % task.id)

    # ==================================================================
    # Reporte nativo (PDF) — preview/descarga para el técnico
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/report', type='http', auth='public',
                website=True, sitemap=False)
    def field_task_report(self, task_id, **kw):
        employee = self._current_employee()
        task = self._task_for_employee(task_id, employee) if employee else None
        if not task:
            return request.redirect('/visar/field')
        pdf, _ctype = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            FSM_REPORT, [task.id])
        return request.make_response(pdf, [
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', 'inline; filename="reporte-servicio.pdf"'),
        ])

    # ==================================================================
    # Utilidades
    # ==================================================================
    @staticmethod
    def _decode_data_url(value):
        """Convierte un data-URL (canvas de firma) a base64 puro para Binary."""
        if not value:
            return False
        if ',' in value:
            value = value.split(',', 1)[1]
        return value or False
