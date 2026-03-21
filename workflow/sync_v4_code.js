// ============================================================
// SYNC v4 — Full Catalog Processor (54,391 productos)
// Uses categoria={id} endpoint for complete coverage
// Fixes: UOM update error, full 12 categories, batch offset
// ============================================================

const ODOO_URL = "https://ocean-tech-0326.odoo.com/jsonrpc";
const DB = "ocean-tech-0326", UID = 2, PWD = "M1ercole$";
const INCOME_ACCOUNT = 145;

// 12 Syscom categories with their IDs and Odoo mappings
const CATS = [
  { id: 22,    name: "Videovigilancia",         categ_id: 4,   expense: 128 },
  { id: 25,    name: "Radiocomunicación",       categ_id: 11,  expense: 132 },
  { id: 26,    name: "Redes e IT",              categ_id: 11,  expense: 132 },
  { id: 27,    name: "IoT / GPS / Telemática",  categ_id: 11,  expense: 132 },
  { id: 30,    name: "Energía / Herramientas",  categ_id: 94,  expense: 143 },
  { id: 32,    name: "Automatización e Intrusión", categ_id: 516, expense: 137 },
  { id: 37,    name: "Control de Acceso",       categ_id: 7,   expense: 131 },
  { id: 38,    name: "Detección de Fuego",      categ_id: 9,   expense: 129 },
  { id: 65747, name: "Marketing",               categ_id: 14,  expense: 128 },
  { id: 65811, name: "Cableado Estructurado",   categ_id: 10,  expense: 133 },
  { id: 66523, name: "Audio y Video",           categ_id: 167, expense: 132 },
  { id: 66630, name: "Industria / BMS / Robots", categ_id: 516, expense: 137 },
];

const UOM_MAP = {
  "Pieza": 44, "Piezas": 44, "pieza": 44, "pza": 44, "Pza": 44, "PZA": 44,
  "Unidad": 27, "Unidades": 27, "Units": 1, "unidad": 27,
  "Bobina 100 mts": 39, "Bobina 305 mts": 40, "Bobina 1000 mts": 41, "Bobina 152 mts": 42,
  "Metro": 5, "Metros": 5, "metro": 5, "mts": 5,
  "Rollo": 39, "rollo": 39, "Par": 44, "Juego": 44, "Kit": 44, "Set": 44,
};

// === Helpers ===
async function odoo(model, method, args, kwargs = {}) {
  const resp = await this.helpers.httpRequest({
    method: "POST", url: ODOO_URL, json: true,
    body: { jsonrpc: "2.0", method: "call", id: Date.now(),
      params: { service: "object", method: "execute_kw",
        args: [DB, UID, PWD, model, method, args], kwargs } }
  });
  if (resp.error) throw new Error(JSON.stringify(resp.error.data || resp.error).substring(0, 500));
  return resp.result;
}

async function imgToBase64(url) {
  if (!url) return null;
  try {
    const resp = await this.helpers.httpRequest({
      method: "GET", url, encoding: "arraybuffer",
      options: { timeout: 15000 }
    });
    if (resp && resp.length) return Buffer.from(resp).toString("base64");
    return null;
  } catch(e) { return null; }
}

const num = (v, d=0) => { const n = Number(v); return Number.isFinite(n) ? n : d; };

// === Read position from staticData ===
const sd = $getWorkflowStaticData("global");
let catIdx = sd.categoryIndex || 0;
let page = sd.page || 1;
let offset = sd.offset || 0;

// Initialize counters if not present
if (!sd.totalCreated) sd.totalCreated = 0;
if (!sd.totalUpdated) sd.totalUpdated = 0;
if (!sd.totalErrors) sd.totalErrors = 0;
if (!sd.totalProcessed) sd.totalProcessed = 0;

// Check if all done
if (catIdx >= CATS.length) {
  return [{ json: {
    _done: true,
    message: "Todas las categorías completadas",
    totalCreated: sd.totalCreated,
    totalUpdated: sd.totalUpdated,
    totalErrors: sd.totalErrors,
    totalProcessed: sd.totalProcessed
  }}];
}

const cat = CATS[catIdx];
const BATCH_SIZE = 30;

console.log("=== BATCH: cat[" + catIdx + "] " + cat.name + " (id:" + cat.id + ") page " + page + " offset " + offset + " ===");

// === 1. Get Syscom Token ===
let token;
try {
  const tokenResp = await this.helpers.httpRequest({
    method: "POST",
    url: "https://developers.syscom.mx/oauth/token",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "client_id=zq2u2Zr1VFGam5IzAg5UolwmeSnsChP7&client_secret=thdUXtjZunSRT4IB1ascUHIZlWsA5PhtIL9mBWVz&grant_type=client_credentials"
  });
  token = tokenResp.access_token;
} catch(e) {
  return [{ json: { status: "error", error: "Token failed: " + e.message, catIdx, page } }];
}

// === 2. Fetch products from Syscom using categoria={id} ===
let allProductos = [];
let lastPage = false;
let totalPages = 0;
try {
  const url = "https://developers.syscom.mx/api/v1/productos?categoria=" + cat.id + "&page=" + page + "&moneda=MXN";
  const resp = await this.helpers.httpRequest({
    method: "GET", url,
    headers: { "Authorization": "Bearer " + token },
    json: true,
    options: { timeout: 30000 }
  });
  allProductos = resp.productos || [];
  totalPages = resp.paginas || 0;
  if (totalPages > 0 && page >= totalPages) lastPage = true;
  if (allProductos.length === 0) lastPage = true;
} catch(e) {
  // Rate limit or other error — don't advance, retry next time
  return [{ json: { status: "error", error: "Syscom fetch failed: " + e.message, catIdx, catName: cat.name, page } }];
}

if (allProductos.length === 0) {
  sd.categoryIndex = catIdx + 1;
  sd.page = 1;
  sd.offset = 0;
  return [{ json: { _empty: true, catIdx, catName: cat.name, page, message: "Sin productos, avanzando" } }];
}

// Apply offset and batch limit
let productos = allProductos.slice(offset, offset + BATCH_SIZE);
const remainingOnPage = allProductos.length - (offset + productos.length);

if (productos.length === 0) {
  // Offset exceeded products on this page - advance
  if (lastPage) {
    sd.categoryIndex = catIdx + 1;
    sd.page = 1;
  } else {
    sd.page = page + 1;
  }
  sd.offset = 0;
  return [{ json: { _empty: true, catIdx, catName: cat.name, page, offset, message: "Offset exceeded, advancing" } }];
}

// === 3. Process each product ===
const results = [];
let created = 0, updated = 0, errors = 0;

for (const p of productos) {
  try {
    const sku = String(p.modelo || "").trim();
    if (!sku) continue;

    const nombre = String(p.titulo || "Producto sin nombre");
    const costo = num(p.precios?.precio_descuento ?? p.precios?.precio_1 ?? 0);
    const listPrice = costo > 0 ? (costo * 1.16) / 0.7 : 0;
    const satKey = String(p.sat_key || "");
    const marca = String(p.marca || "");
    const imgUrl = String(p.img_portada || "");

    const uomNombre = p.unidad_de_medida?.nombre || "Pieza";
    const uomId = UOM_MAP[uomNombre] || 44;

    // UNSPSC lookup
    let unspscId = false;
    if (satKey && satKey !== "null" && satKey !== "undefined" && satKey.length >= 4) {
      try {
        const sat = await odoo.call(this, "product.unspsc.code", "search_read",
          [[["code","=",satKey]]], {fields:["id"], limit:1});
        if (sat && sat.length) unspscId = sat[0].id;
      } catch(e) { /* skip UNSPSC if lookup fails */ }
    }

    // Image
    let imageB64 = await imgToBase64.call(this, imgUrl);

    // Search existing by default_code OR barcode
    let existing = await odoo.call(this, "product.template", "search_read",
      [[["default_code","=",sku]]], {fields:["id","default_code","barcode"], limit:1});
    if (!existing || !existing.length) {
      existing = await odoo.call(this, "product.template", "search_read",
        [[["barcode","=",sku]]], {fields:["id","default_code","barcode"], limit:1});
    }

    let op, prodId;

    if (existing && existing.length) {
      // === UPDATE existing product ===
      prodId = existing[0].id;
      const updateVals = {
        name: nombre,
        standard_price: costo,
        list_price: Math.round(listPrice * 100) / 100,
        categ_id: cat.categ_id,
        // NO uom_id on updates — causes "asientos contables" error
        property_account_income_id: INCOME_ACCOUNT,
        property_account_expense_id: cat.expense,
        description_sale: marca ? "Marca: " + marca : "",
        sale_ok: true,
        purchase_ok: true,
      };
      if (unspscId) updateVals.unspsc_code_id = unspscId;
      if (imageB64) updateVals.image_1920 = imageB64;

      await odoo.call(this, "product.template", "write", [[prodId], updateVals]);
      op = "update";
      updated++;
      // Free memory
      imageB64 = null;
      if (updateVals.image_1920) delete updateVals.image_1920;
    } else {
      // === CREATE new product ===
      const createVals = {
        name: nombre,
        default_code: sku,
        barcode: sku,
        standard_price: costo,
        list_price: Math.round(listPrice * 100) / 100,
        categ_id: cat.categ_id,
        uom_id: uomId,
        property_account_income_id: INCOME_ACCOUNT,
        property_account_expense_id: cat.expense,
        description_sale: marca ? "Marca: " + marca : "",
        sale_ok: true,
        purchase_ok: true,
      };
      if (unspscId) createVals.unspsc_code_id = unspscId;
      if (imageB64) createVals.image_1920 = imageB64;

      prodId = await odoo.call(this, "product.template", "create", [createVals]);
      op = "create";
      created++;
      // Free memory
      imageB64 = null;
      if (createVals.image_1920) delete createVals.image_1920;
    }

    results.push({ json: { sku, op, prodId, status: "ok" } });
  } catch(err) {
    errors++;
    results.push({ json: { sku: p.modelo || "?", status: "error", error: err.message.substring(0,200) } });
  }
}

// === 4. Advance position ===
if (remainingOnPage > 0) {
  // More products on this page — advance offset
  sd.offset = offset + BATCH_SIZE;
} else if (lastPage) {
  // Last page of this category — advance to next category
  sd.categoryIndex = catIdx + 1;
  sd.page = 1;
  sd.offset = 0;
} else {
  // More pages — advance page
  sd.page = page + 1;
  sd.offset = 0;
}

// Update global counters
sd.totalCreated += created;
sd.totalUpdated += updated;
sd.totalErrors += errors;
sd.totalProcessed += (created + updated + errors);

// Add summary as first item
const summary = {
  json: {
    _summary: true,
    category: cat.name,
    categoryId: cat.id,
    categoryIndex: catIdx,
    page, offset,
    totalPages,
    nextCategoryIndex: sd.categoryIndex,
    nextPage: sd.page,
    nextOffset: sd.offset,
    batchSize: productos.length,
    created, updated, errors,
    total: created + updated + errors,
    lastPage,
    globalTotals: {
      created: sd.totalCreated,
      updated: sd.totalUpdated,
      errors: sd.totalErrors,
      processed: sd.totalProcessed
    }
  }
};

return [summary, ...results];
