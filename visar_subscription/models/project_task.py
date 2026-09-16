"""Visitas de una póliza: de qué tipo son, qué número les toca y para cuándo.

Una póliza no genera un solo tipo de visita, y tratarlas como si lo hiciera es lo que
hacía imposible saber qué le debe Visar a cada cliente:

* Las **preventivas** son la serie que el cliente compró: una cada tanto, contadas
  ("3 de 12"). Son las únicas que se numeran, las únicas que llevan fecha propuesta y
  las únicas que aparecen en el rezago de "visitas por agendar".
* Las **correctivas** son las visitas de refuerzo de las primeras semanas, cuando se
  contrata con plaga activa: cuántas hagan falta para erradicarla, incluidas en el
  mismo precio. No consumen la serie ni la recorren.
* Las de **garantía** son la reincidencia dentro de la ventana de 30 días, que ya
  existía con su propio botón en el pedido y su propia tasa de siniestralidad.

Mezclar las tres tenía dos costos concretos: una correctiva registrada como garantía
inflaba `visar_warranty_rate` (se mide para ajustar el precio en la renovación) y una
registrada como visita normal le comía al cliente una de las visitas que pagó.

`visar_is_warranty` se conserva —lo usan el botón de garantía, la siniestralidad y las
búsquedas de otros módulos— pero pasa a DERIVARSE del tipo, para que no haya dos campos
que puedan contradecirse.
"""
from odoo import api, fields, models

VISIT_KINDS = [
    ('preventiva', "Preventiva (serie de la póliza)"),
    ('correctiva', "Correctiva (refuerzo, no consume la serie)"),
    ('garantia', "Garantía (reincidencia, sin costo)"),
]


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_subscription_order_id = fields.Many2one(
        'sale.order',
        string="Póliza (suscripción)",
        index=True,
        ondelete='set null',
        copy=False,
        help="Póliza / suscripción que originó esta visita de servicio.",
    )
    visar_source_invoice_id = fields.Many2one(
        'account.move',
        string="Factura de periodo",
        index=True,
        ondelete='set null',
        copy=False,
        help="Factura del periodo de la póliza que generó esta visita.",
    )
    visar_source_line_id = fields.Many2one(
        'sale.order.line',
        string="Línea de póliza",
        index=True,
        ondelete='set null',
        copy=False,
        help="Línea REPRESENTANTE de la póliza que generó esta visita. En una visita "
             "consolidada (fumigación + áreas verdes en la misma vuelta) es solo una "
             "de las dos; el conjunto completo está en «Líneas de póliza cubiertas».",
    )
    # Todas las líneas que atiende la visita. Es la que manda: la idempotencia por
    # (orden, factura, grupo) y el etiquetado de grupos de servicio salen de aquí.
    #
    # No se reusa `visar_sale_line_ids` (el o2m sobre `sale.order.line.task_id` del
    # que cuelga la venta puntual): la línea de una póliza genera N visitas a lo
    # largo del contrato y ese m2o solo puede apuntar a una.
    visar_source_line_ids = fields.Many2many(
        'sale.order.line',
        'visar_task_poliza_line_rel', 'task_id', 'line_id',
        string="Líneas de póliza cubiertas",
        copy=False,
        help="Todas las líneas de la póliza que atiende esta visita. Una visita "
             "consolidada lleva las dos (o más); una normal, solo la suya.",
    )
    visar_visit_kind = fields.Selection(
        VISIT_KINDS,
        string="Tipo de visita",
        default='preventiva',
        index=True,
        copy=False,
        help="Preventiva: una de las visitas que incluye la póliza.\n"
             "Correctiva: refuerzo para erradicar una plaga activa; va incluida en el "
             "precio, no consume las visitas del cliente y no recorre la serie.\n"
             "Garantía: reincidencia dentro de la ventana de garantía.",
    )
    visar_is_warranty = fields.Boolean(
        string="Visita de garantía",
        compute='_compute_visar_is_warranty',
        inverse='_inverse_visar_is_warranty',
        store=True,
        copy=False,
        help="Visita adicional sin costo cubierta por la garantía de la póliza. "
             "Se deriva del tipo de visita.",
    )
    visar_visit_seq = fields.Integer(
        string="Nº de visita",
        copy=False,
        help="Qué número de visita es dentro del lote que generó su factura. Es el "
             "mismo consecutivo que ya aparecía en el título, pero en un campo, para "
             "poder ordenar y filtrar por él.",
    )
    visar_visit_total = fields.Integer(
        string="Visitas del lote",
        copy=False,
        help="Cuántas visitas generó la factura de la que sale esta. Con el nº de "
             "visita forma el «3 de 12» del título.",
    )
    visar_visit_due_date = fields.Date(
        string="Fecha propuesta",
        index=True,
        copy=False,
        help="Cuándo le TOCA esta visita, mientras no tenga fecha agendada. Sale de "
             "la fecha real de la visita anterior más los meses entre visitas del "
             "plan. Se puede escribir a mano: a partir de ahí esta visita queda fija "
             "y las siguientes se proponen desde ella.",
    )
    visar_visit_due_manual = fields.Boolean(
        string="Fecha propuesta a mano",
        copy=False,
        help="La fecha propuesta la escribió una persona, así que el recálculo "
             "automático de la serie no la pisa.",
    )
    visar_visit_due_out_of_term = fields.Boolean(
        string="Fuera de vigencia",
        copy=False,
        help="A esta visita le tocaría una fecha posterior al fin de la póliza. Se "
             "marca en vez de proponer una fecha que ya no cabe en el contrato: son "
             "las visitas acumuladas que alguien tiene que decidir qué hacer con "
             "ellas.",
    )
    visar_visit_days_late = fields.Integer(
        string="Días de retraso",
        compute='_compute_visar_visit_days_late',
        help="Días transcurridos desde la fecha propuesta. Negativo = todavía no le "
             "toca.",
    )
    visar_poliza_end_date = fields.Date(
        string="Fin de la póliza",
        related='visar_subscription_order_id.end_date',
        readonly=True,
    )

    @api.depends('visar_visit_kind')
    def _compute_visar_is_warranty(self):
        for task in self:
            task.visar_is_warranty = task.visar_visit_kind == 'garantia'

    def _inverse_visar_is_warranty(self):
        """Escribir el booleano sigue funcionando: mueve el TIPO, que es la fuente.

        Lo escriben el botón de garantía del pedido y código anterior a los tipos.
        Apagarlo devuelve la visita a preventiva —la única lectura posible cuando lo
        que se dice es "esto ya no es garantía"— y por eso una correctiva no se toca
        salvo que alguien encienda el flag.
        """
        for task in self:
            if task.visar_is_warranty:
                task.visar_visit_kind = 'garantia'
            elif task.visar_visit_kind == 'garantia':
                task.visar_visit_kind = 'preventiva'

    @api.depends('visar_visit_due_date')
    def _compute_visar_visit_days_late(self):
        hoy = fields.Date.context_today(self)
        for task in self:
            task.visar_visit_days_late = (
                (hoy - task.visar_visit_due_date).days
                if task.visar_visit_due_date else 0)

    @api.depends('visar_sale_line_ids.product_id', 'visar_sale_line_ids.order_id',
                 'sale_order_id', 'visar_source_line_ids.product_id')
    def _compute_visar_service_group_ids(self):
        """Las visitas de póliza etiquetan sus grupos desde `visar_source_line_ids`.

        El cómputo de `visar_fsm` sale de `sale.order.line.task_id`, que una visita de
        póliza nunca tiene (ver arriba). Sin esto una visita consolidada no contaría en
        ninguna línea de negocio y el tablero de FSM la dejaría fuera al agrupar por
        servicio — justo la métrica que la consolidación existe para no romper.

        El decorador REPITE las dependencias de la superclase a propósito: Odoo resuelve
        `depends` desde el método que encuentra en el modelo final, así que las de allá
        se perderían si aquí no se listan.
        """
        super()._compute_visar_service_group_ids()
        for task in self.filtered('visar_source_line_ids'):
            templates = task.visar_source_line_ids.mapped('product_id.product_tmpl_id')
            task.visar_service_group_ids = (
                templates._visar_service_groups() if templates else False)

    # ------------------------------------------------------------------
    # La serie se recalcula sola cuando cambia algo que la mueve
    # ------------------------------------------------------------------
    # Las fechas propuestas no son un compute con `depends`: dependerían de las OTRAS
    # visitas de la misma póliza (una cadena, no un campo) y cualquier escritura en el
    # lote dispararía el recálculo de todo el contrato. Se llama explícito desde donde
    # la serie cambia de verdad: se agenda una visita, se cambia su tipo, o nace otra.
    _VISAR_DUE_TRIGGERS = ('planned_date_begin', 'visar_visit_kind',
                           'visar_is_warranty', 'state')

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks._visar_sync_poliza_due_dates()
        return tasks

    def write(self, vals):
        # Una fecha propuesta escrita a mano deja de ser propuesta: el recálculo la
        # respeta y encadena las siguientes a partir de ella. Sin esto, agendar
        # cualquier otra visita de la póliza borraría el ajuste en silencio.
        if ('visar_visit_due_date' in vals
                and not self.env.context.get('visar_poliza_due_sync')
                and 'visar_visit_due_manual' not in vals):
            vals = dict(vals, visar_visit_due_manual=bool(vals['visar_visit_due_date']))
        res = super().write(vals)
        if any(campo in vals for campo in self._VISAR_DUE_TRIGGERS):
            self._visar_sync_poliza_due_dates()
        return res

    def _visar_sync_poliza_due_dates(self):
        """Repropone las visitas pendientes de las pólizas que toca este recordset."""
        if self.env.context.get('visar_poliza_due_sync'):
            return
        ordenes = self.mapped('visar_subscription_order_id')
        if ordenes:
            ordenes._visar_schedule_visit_due_dates()
