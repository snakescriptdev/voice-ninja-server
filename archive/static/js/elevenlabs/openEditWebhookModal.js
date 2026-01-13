/*************************************************
 *  WebhookToolEditor.js
 *  Clean, Class-Based Renderer + Extractor
 *************************************************/

class WebhookToolEditor {
    constructor(rawConfig, functionData) {
        this.raw = rawConfig?.tool_config || {};
        this.funcData = functionData;
        this.api = this.raw.api_schema || {};
    }

    /* ================================
     *  Value Type Detection
     * ================================ */
    detectValueType(config = {}) {
        if (config.constant_value !== undefined) return "constant_value";
        if (config.dynamic_variable !== undefined) return "dynamic_variable";
        if (config.is_system_provided === true) return "system_variable";
        return "llm_prompt";
    }

    /* ================================
     *  Extractor – Flattens API Schema
     * ================================ */
    extract() {
        const bodySchema = this.api.request_body_schema || {};
        const pathProps = this.api.path_params_schema || {};
        const queryProps = this.api.query_params_schema?.properties || {};

        return {
            http_method: this.api.method || "POST",
            body_description: bodySchema.description || "",
            path_params: this.transformObjToArray(pathProps, this.api.path_params_schema),
            query_params: this.transformObjToArray(queryProps, this.api.query_params_schema),
            request_body_properties: this.transformObjToArray(bodySchema.properties || {}, bodySchema),
            request_headers: this.extractHeaders(this.api.request_headers || {}),
            dynamic_variables: this.extractDynamicVars(this.raw.dynamic_variables),
            assignments: this.raw.assignments || [],
            disable_interruptions: this.raw.disable_interruptions || false,
            force_pre_tool_speech: this.raw.force_pre_tool_speech || false
        };
    }

    /* Convert object key → array list for UI */
    transformObjToArray(obj, parentSchema) {
        return Object.entries(obj).map(([name, cfg]) => ({
            name,
            type: cfg.type || "string",
            required: parentSchema?.required?.includes(name) || false,
            constant_value: cfg.constant_value,
            dynamic_variable: cfg.dynamic_variable,
            is_system_provided: cfg.is_system_provided,
            description: cfg.description || "",
            value_type: this.detectValueType(cfg)
        }));
    }

    extractHeaders(headers) {
        return Object.entries(headers).map(([name, value]) => ({
            name,
            value,
            value_type: "constant_value"
        }));
    }

    extractDynamicVars(dynamicVarBlock) {
        const vars = dynamicVarBlock?.dynamic_variable_placeholders || {};
        return Object.entries(vars).map(([name, value]) => ({
            name,
            description: "",
            dynamic_variable: value,
            value_type: "dynamic_variable"
        }));
    }

    /* ================================
     *  HTML Section Generators
     * ================================ */

    /* Generic value-type renderer */
    renderValueInput(type, item, idPrefix) {
        const cid = `${idPrefix}_value`;

        switch (type) {
            case "constant_value":
                return `
                    <label class="form-label">Constant Value</label>
                    <input type="text" class="form-control param-constant-value"
                        value="${item.constant_value || ''}">
                `;
            case "dynamic_variable":
                return `
                    <label class="form-label">Dynamic Variable</label>
                    <input type="text" class="form-control param-dynamic-variable"
                      value="${item.dynamic_variable || ''}">
                `;
            case "system_variable":
                return `
                    <label class="form-label text-primary">System Provided</label>
                    <p class="text-muted mb-0">This is auto-filled by system</p>
                `;
            default:
                return `
                    <label class="form-label">LLM Prompt Description</label>
                    <textarea class="form-control param-description" rows="2">${item.description || ''}</textarea>
                `;
        }
    }

    /* PATH PARAMS */
    renderPathParams(arr) {
        if (!arr.length)
            return `<p class="text-muted">No path parameters defined</p>`;

        return arr.map((item, i) => {
            const id = `edit_path_param_${i}`;
            return `
                <div class="param-item border rounded p-3 mb-3" id="${id}">
                    <div class="d-flex justify-content-between mb-2">
                        <h6 class="mb-0">Path Parameter</h6>
                        <button class="btn btn-sm btn-outline-danger"
                            onclick="removeEditPathParam(${i})">Delete</button>
                    </div>

                    <div class="row">
                        <div class="col-md-2">
                            <label class="form-label">Type</label>
                            <select class="form-select param-type">
                                ${this.renderTypeOptions(item.type)}
                            </select>
                        </div>

                        <div class="col-md-3">
                            <label class="form-label">Identifier</label>
                            <input class="form-control param-name" value="${item.name}">
                        </div>

                        <div class="col-md-5">
                            <label class="form-label">Value Type</label>
                            <select class="form-select param-value-type"
                                onchange="toggleEditPathParamValueType('${id}')">
                                ${this.renderValueTypeOptions(item.value_type)}
                            </select>
                        </div>
                    </div>

                    <div class="row mt-2"><div class="col-12" id="${id}_value_container">
                        ${this.renderValueInput(item.value_type, item, id)}
                    </div></div>
                </div>
            `;
        }).join("");
    }

    /* QUERY PARAMS */
    renderQueryParams(arr) {
        if (!arr.length)
            return `<p class="text-muted">No query parameters defined</p>`;

        return arr.map((item, i) => {
            const id = `edit_query_param_${i}`;
            return `
                <div class="param-item border rounded p-3 mb-3" id="${id}">
                    <div class="d-flex justify-content-between mb-2">
                        <h6 class="mb-0">Query Parameter</h6>
                        <button class="btn btn-sm btn-outline-danger" onclick="removeEditQueryParam(${i})">Delete</button>
                    </div>

                    <div class="row">
                        <div class="col-md-2">
                           <label class="form-label">Type</label>
                           <select class="form-select param-type">
                              ${this.renderTypeOptions(item.type)}
                           </select>
                        </div>

                        <div class="col-md-3">
                           <label class="form-label">Identifier</label>
                           <input class="form-control param-name" value="${item.name}">
                        </div>

                        <div class="col-md-5">
                           <label class="form-label">Value Type</label>
                           <select class="form-select param-value-type"
                              onchange="toggleEditQueryParamValueType('${id}')">
                              ${this.renderValueTypeOptions(item.value_type)}
                           </select>
                        </div>
                    </div>

                    <div class="row mt-2"><div class="col-12" id="${id}_value_container">
                        ${this.renderValueInput(item.value_type, item, id)}
                    </div></div>
                </div>`;
        }).join("");
    }

    /* REQUEST BODY */
    renderBodyProperties(arr) {
        if (!arr.length)
            return `<p class="text-muted">No properties defined</p>`;

        return arr.map((item, i) => {
            const id = `edit_property_${i}`;
            return `
                <div class="param-item border rounded p-3 mb-3" id="${id}">
                    <div class="d-flex justify-content-between mb-2">
                        <h6 class="mb-0">Request Body Property</h6>
                        <button class="btn btn-sm btn-outline-danger"
                            onclick="removeEditRequestBodyProperty(${i})">Delete</button>
                    </div>

                    <div class="row">
                        <div class="col-md-2">
                            <label class="form-label">Type</label>
                            <select class="form-select param-type">
                                ${this.renderTypeOptions(item.type)}
                            </select>
                        </div>

                        <div class="col-md-3">
                            <label class="form-label">Identifier</label>
                            <input class="form-control param-name" value="${item.name}">
                        </div>

                        <div class="col-md-2">
                            <label class="form-check-label mt-4">
                                <input type="checkbox" class="form-check-input param-required"
                                    ${item.required ? 'checked' : ''}>
                                Required
                            </label>
                        </div>

                        <div class="col-md-5">
                            <label class="form-label">Value Type</label>
                            <select class="form-select param-value-type"
                                onchange="toggleEditBodyPropertyValueType('${id}')">
                                ${this.renderValueTypeOptions(item.value_type)}
                            </select>
                        </div>
                    </div>

                    <div class="row mt-2"><div class="col-12" id="${id}_value_container">
                        ${this.renderValueInput(item.value_type, item, id)}
                    </div></div>
                </div>`;
        }).join("");
    }

    /* HEADERS */
    renderHeaders(arr) {
        if (!arr.length)
            return `<p class="text-muted">No headers defined</p>`;

        return arr.map((h, i) => `
            <div class="row mb-2 header-row" data-index="${i}">
                <div class="col-md-4">
                    <input class="form-control" value="${h.name}">
                </div>
                <div class="col-md-6">
                    <input class="form-control" value="${h.value}">
                </div>
                <div class="col-md-2">
                    <button class="btn btn-sm btn-outline-danger"
                        onclick="removeEditRequestHeader(${i})">×</button>
                </div>
            </div>
        `).join("");
    }

    /* DYNAMIC VARIABLES */
    renderDynamicVars(arr) {
        if (!arr.length)
            return `<p class="text-muted">No dynamic variables defined</p>`;

        return arr.map((v, i) => `
            <div class="row mb-2 variable-row" data-index="${i}">
                <div class="col-md-4">
                    <input class="form-control" value="${v.name}">
                </div>
                <div class="col-md-6">
                    <input class="form-control" value="${v.dynamic_variable}">
                </div>
                <div class="col-md-2">
                    <button class="btn btn-sm btn-outline-danger"
                        onclick="removeEditDynamicVariable(${i})">×</button>
                </div>
            </div>
        `).join("");
    }

    /* ASSIGNMENTS */
    renderAssignments(arr) {
        if (!arr.length)
            return `<p class="text-muted">No assignments defined</p>`;

        return arr.map((as, i) => `
            <div class="row mb-2 assignment-row" data-index="${i}">
                <div class="col-md-4">
                    <input class="form-control" value="${as.dynamic_variable}">
                </div>
                <div class="col-md-6">
                    <input class="form-control" value="${as.value_path}">
                </div>
                <div class="col-md-2">
                    <button class="btn btn-sm btn-outline-danger"
                        onclick="removeEditAssignment(${i})">×</button>
                </div>
            </div>
        `).join("");
    }

    /* ================================
     *  Helpers
     * ================================ */
    renderTypeOptions(selected) {
        const list = ["string", "integer", "number", "boolean", "object", "array"];
        return list.map(t => `<option value="${t}" ${selected === t ? 'selected' : ''}>${t}</option>`).join("");
    }

    renderValueTypeOptions(selected) {
        const list = [
            { key: "llm_prompt", label: "LLM Prompt" },
            { key: "constant_value", label: "Constant Value" },
            { key: "dynamic_variable", label: "Dynamic Variable" }
        ];

        return list.map(i => `<option value="${i.key}" ${selected === i.key ? 'selected' : ''}>${i.label}</option>`).join("");
    }
}

/*************************************************
 * Entry Point called inside editFunction()
 *************************************************/
function extractToolConfigForEdit(rawToolConfig, functionData) {
    const tool = new WebhookToolEditor(rawToolConfig, functionData);
    return tool.extract();
}

/* Generator helpers below call class methods */

function generatePathParamsHTML(params) {
    const tool = new WebhookToolEditor({});
    return tool.renderPathParams(params);
}

function generateQueryParamsHTML(params) {
    const tool = new WebhookToolEditor({});
    return tool.renderQueryParams(params);
}

function generateRequestBodyPropertiesHTML(params) {
    const tool = new WebhookToolEditor({});
    return tool.renderBodyProperties(params);
}

function generateRequestHeadersHTML(params) {
    const tool = new WebhookToolEditor({});
    return tool.renderHeaders(params);
}

function generateDynamicVariablesHTML(params) {
    const tool = new WebhookToolEditor({});
    return tool.renderDynamicVars(params);
}

function generateAssignmentsHTML(params) {
    const tool = new WebhookToolEditor({});
    return tool.renderAssignments(params);
}
