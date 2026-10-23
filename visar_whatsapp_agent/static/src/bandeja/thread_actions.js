/** Bandeja de WhatsApp en Odoo (23-oct-2026): «Tomar» y «Devolver al agente»
 * en la cabecera de los chats de WhatsApp. El trabajo lo hace el servidor
 * (`discuss.channel.action_visar_tomar` / `action_visar_devolver`), que
 * contesta con el aviso que se muestra. */
import { registerThreadAction } from "@mail/core/common/thread_actions";

import { _t } from "@web/core/l10n/translation";

const esWhatsApp = ({ owner, thread }) =>
    thread?.channel_type === "whatsapp" && !owner.isDiscussSidebarChannelActions;

async function llamar(store, thread, metodo) {
    const accion = await store.env.services.orm.call("discuss.channel", metodo, [[thread.id]]);
    if (accion) {
        await store.env.services.action.doAction(accion);
    }
}

registerThreadAction("visar-tomar", {
    condition: esWhatsApp,
    open: ({ store, thread }) => llamar(store, thread, "action_visar_tomar"),
    icon: "fa fa-fw fa-hand-paper-o",
    name: _t("Tomar la conversación"),
    sequenceGroup: 1,
});

registerThreadAction("visar-devolver", {
    condition: esWhatsApp,
    open: ({ store, thread }) => llamar(store, thread, "action_visar_devolver"),
    icon: "fa fa-fw fa-reply",
    name: _t("Devolver al agente"),
    sequenceGroup: 1,
});
