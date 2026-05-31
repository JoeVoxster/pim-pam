var dagcomponentfuncs = window.dashAgGridComponentFunctions = window.dashAgGridComponentFunctions || {};

dagcomponentfuncs.ProductSelectFilterHeader = function (props) {
    const values = (props.values || []).filter(function (value) { return value !== null && value !== undefined; });
    const labels = props.labels || {};
    const field = props.column && props.column.getColDef ? props.column.getColDef().field : null;
    const [selected, setSelected] = React.useState('');
    const applyFilter = function (value) {
        if (!props.api || !field) {
            return;
        }
        const currentModel = props.api.getFilterModel ? (props.api.getFilterModel() || {}) : {};
        if (!value) {
            delete currentModel[field];
        } else {
            currentModel[field] = {
                filterType: 'text',
                type: 'equals',
                filter: value
            };
        }
        props.api.setFilterModel(currentModel);
        if (props.api.onFilterChanged) {
            props.api.onFilterChanged();
        }
    };
    const stopGridEvent = function (event) {
        event.stopPropagation();
    };
    return React.createElement(
        'div',
        {
            onClick: stopGridEvent,
            onMouseDown: stopGridEvent,
            style: {
                display: 'flex',
                flexDirection: 'column',
                gap: '2px',
                width: '100%',
                minWidth: 0,
                padding: '2px 0'
            }
        },
        React.createElement(
            'span',
            {
                style: {
                    fontSize: '11px',
                    fontWeight: '700',
                    lineHeight: '1.1',
                    color: '#334155'
                }
            },
            props.displayName || ''
        ),
        React.createElement(
            'select',
            {
                value: selected,
                onClick: stopGridEvent,
                onMouseDown: stopGridEvent,
                onChange: function (event) {
                    const value = event.target.value;
                    setSelected(value);
                    applyFilter(value);
                },
                style: {
                    width: '100%',
                    minWidth: 0,
                    height: '22px',
                    border: '1px solid #cbd5e1',
                    borderRadius: '4px',
                    background: '#ffffff',
                    color: '#0f172a',
                    fontSize: '11px',
                    padding: '0 2px'
                }
            },
            React.createElement('option', {value: ''}, props.allLabel || 'Alle'),
            values.map(function (value) {
                return React.createElement('option', {key: value, value: value}, labels[value] || value);
            })
        )
    );
};

dagcomponentfuncs.CategoryToggleButton = function (props) {
    const value = props.value || '';
    const hasChildren = !!(props.data && props.data.has_children);
    if (!hasChildren) {
        return React.createElement('span', {style: {color: '#94a3b8', fontSize: '16px'}}, value || '•');
    }
    const onClick = function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (props.setData) {
            props.setData({action: 'toggle_category', category_id: props.data.id, ts: Date.now()});
        }
    };
    return React.createElement(
        'button',
        {
            onClick: onClick,
            title: 'Kategorie ein-/ausklappen',
            style: {
                border: '1px solid #cbd5e1',
                background: '#ffffff',
                borderRadius: '8px',
                width: '28px',
                height: '28px',
                lineHeight: '24px',
                padding: '0',
                cursor: 'pointer',
                fontSize: '16px'
            }
        },
        value || '▸'
    );
};

dagcomponentfuncs.CategoryDropTargetCell = function (props) {
    const value = props.value || '';
    const onDragOver = function (event) {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
    };
    const onDrop = function (event) {
        event.preventDefault();
        event.stopPropagation();
        let payload = null;
        try {
            payload = JSON.parse(event.dataTransfer.getData('application/json') || '{}');
        } catch (error) {
            payload = null;
        }
        const productIds = payload && Array.isArray(payload.product_ids) ? payload.product_ids : [];
        if (!productIds.length || !props.setData || !props.data || !props.data.id) {
            return;
        }
        props.setData({
            action: 'move_products_to_category',
            category_id: props.data.id,
            product_ids: productIds,
            ts: Date.now()
        });
    };
    return React.createElement(
        'span',
        {
            onDragOver: onDragOver,
            onDrop: onDrop,
            title: 'Produkte hierher verschieben',
            style: {
                display: 'block',
                minHeight: '28px',
                lineHeight: '28px',
                padding: '0 4px'
            }
        },
        value
    );
};


dagcomponentfuncs.ProductCategoryDragCell = function (props) {
    const value = props.value || '';
    const dragPayload = function () {
        const selectedRows = props.api && props.api.getSelectedRows ? props.api.getSelectedRows() : [];
        let rows = selectedRows && selectedRows.length ? selectedRows : [props.data || {}];
        const draggedId = props.data && props.data.id;
        if (draggedId && !rows.some(function (row) { return row && row.id === draggedId; })) {
            rows = [props.data || {}];
        }
        const productIds = rows
            .map(function (row) { return row && row.id; })
            .filter(function (id) { return id !== null && id !== undefined && id !== ''; });
        return {product_ids: productIds};
    };
    const onDragStart = function (event) {
        const payload = dragPayload();
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('application/json', JSON.stringify(payload));
        event.dataTransfer.setData('text/plain', payload.product_ids.join(','));
    };
    const onDragOver = function (event) {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
    };
    const onDrop = function (event) {
        event.preventDefault();
        event.stopPropagation();
        let payload = null;
        try {
            payload = JSON.parse(event.dataTransfer.getData('application/json') || '{}');
        } catch (error) {
            payload = null;
        }
        const productIds = payload && Array.isArray(payload.product_ids) ? payload.product_ids : [];
        const targetId = props.data && props.data.id;
        if (!productIds.length || !targetId || !props.setData) {
            return;
        }
        const rect = event.currentTarget.getBoundingClientRect();
        const position = event.clientY > rect.top + rect.height / 2 ? 'after' : 'before';
        props.setData({
            action: 'reorder_products_in_category',
            target_product_id: targetId,
            product_ids: productIds,
            position: position,
            ts: Date.now()
        });
    };
    return React.createElement(
        'span',
        {
            draggable: true,
            onDragStart: onDragStart,
            onDragOver: onDragOver,
            onDrop: onDrop,
            title: 'Produkt ziehen: auf Kategorie verschieben oder auf Produktposition einsortieren',
            style: {
                cursor: 'grab',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                minHeight: '28px',
                width: '100%'
            }
        },
        React.createElement('span', {style: {color: '#64748b', fontWeight: 700}}, '↕'),
        React.createElement('span', null, value)
    );
};

dagcomponentfuncs.ProductTitleButton = function (props) {
    const value = props.value || '';
    const onClick = function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (props.setData && props.data) {
            props.setData({action: 'activate_product', row: props.data, product_id: props.data.id, ts: Date.now()});
        }
    };
    return React.createElement(
        'button',
        {
            onClick: onClick,
            title: 'Produktdetails laden',
            style: {
                border: 'none',
                background: 'transparent',
                padding: '0',
                margin: '0',
                color: '#1d4ed8',
                cursor: 'pointer',
                textAlign: 'left',
                font: 'inherit'
            }
        },
        value || ''
    );
};


dagcomponentfuncs.ProductIdLinkCell = function (props) {
    const productId = props.data && props.data.product_id;
    if (productId === null || productId === undefined || productId === '') {
        return React.createElement('span', {style: {color: '#94a3b8'}}, '-');
    }
    const onClick = function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (props.setData) {
            props.setData({action: 'open_product_from_asset', product_id: productId, ts: Date.now()});
        }
    };
    return React.createElement(
        'button',
        {
            onClick: onClick,
            title: 'Produkt öffnen',
            style: {
                border: 'none',
                background: 'transparent',
                padding: '0',
                margin: '0',
                color: '#2563eb',
                cursor: 'pointer',
                textAlign: 'left',
                font: 'inherit',
                textDecoration: 'underline',
                textDecorationColor: '#bfdbfe',
                textUnderlineOffset: '2px'
            }
        },
        String(productId)
    );
};


dagcomponentfuncs.ProductPhotoCell = function (props) {
    const row = props.data || {};
    const assetId = row.photo_asset_id;
    const href = row.photo_url || (assetId ? '/asset-file/' + assetId : null);
    const thumbHref = row.photo_thumb_url || href;
    const filename = row.photo_filename || 'Produktbild öffnen';
    const mimeType = String(row.photo_mime_type || '').toLowerCase();
    const isImage = href && (mimeType.indexOf('image/') === 0 || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(String(filename)));
    const placeholderStyle = {
        width: '46px',
        height: '46px',
        borderRadius: '10px',
        border: '1px dashed #cbd5e1',
        background: '#f8fafc',
        color: '#94a3b8',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '10px',
        fontWeight: '600',
        lineHeight: '1.1',
        textAlign: 'center'
    };
    if (!href || !isImage) {
        return React.createElement('div', {style: placeholderStyle, title: href ? 'Keine Bildvorschau' : 'Kein Bild'}, 'No image');
    }
    const image = React.createElement('img', {
        src: thumbHref,
        alt: filename,
        onError: function (event) {
            event.currentTarget.style.display = 'none';
            const fallback = event.currentTarget.nextSibling;
            if (fallback) {
                fallback.style.display = 'inline-flex';
            }
        },
        style: {
            width: '46px',
            height: '46px',
            objectFit: 'cover',
            borderRadius: '10px',
            border: '1px solid #cbd5e1',
            background: '#ffffff',
            display: 'block'
        }
    });
    const fallback = React.createElement('div', {style: {...placeholderStyle, display: 'none'}}, 'No image');
    return React.createElement(
        'a',
        {
            href: href,
            target: '_blank',
            rel: 'noreferrer',
            title: filename,
            style: {
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '52px',
                height: '52px',
                textDecoration: 'none',
                cursor: 'pointer'
            }
        },
        image,
        fallback
    );
};


dagcomponentfuncs.VariantIdLinkCell = function (props) {
    const variantId = props.data && props.data.variant_id;
    if (variantId === null || variantId === undefined || variantId === '') {
        return React.createElement('span', {style: {color: '#94a3b8'}}, '-');
    }
    const onClick = function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (props.setData) {
            props.setData({action: 'open_variant_from_asset', variant_id: variantId, product_id: props.data && props.data.product_id, ts: Date.now()});
        }
    };
    return React.createElement(
        'button',
        {
            onClick: onClick,
            title: 'Variante öffnen',
            style: {
                border: 'none',
                background: 'transparent',
                padding: '0',
                margin: '0',
                color: '#2563eb',
                cursor: 'pointer',
                textAlign: 'left',
                font: 'inherit',
                textDecoration: 'underline',
                textDecorationColor: '#bfdbfe',
                textUnderlineOffset: '2px'
            }
        },
        String(variantId)
    );
};


dagcomponentfuncs.AssetPreviewCell = function (props) {
    const row = props.data || {};
    const assetId = row.id;
    if (!assetId) {
        return React.createElement('span', {style: {color: '#94a3b8'}}, '-');
    }
    const href = '/asset-file/' + assetId;
    const mimeType = String(row.mime_type || '').toLowerCase();
    const filename = String(row.filename || '');
    const isImage = mimeType.indexOf('image/') === 0 || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(filename);
    const isPdf = mimeType === 'application/pdf' || /\.pdf$/i.test(filename);
    const linkStyle = {display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '64px', height: '64px', textDecoration: 'none'};
    if (isImage) {
        return React.createElement(
            'a',
            {href: href, target: '_blank', title: filename, style: linkStyle},
            React.createElement('img', {src: href, style: {width: '56px', height: '56px', objectFit: 'cover', borderRadius: '8px', border: '1px solid #cbd5e1', background: '#fff'}})
        );
    }
    if (isPdf) {
        return React.createElement(
            'a',
            {href: href, target: '_blank', title: 'PDF öffnen', style: linkStyle},
            React.createElement('div', {style: {width: '56px', height: '56px', borderRadius: '10px', border: '1px solid #fecaca', background: '#fef2f2', color: '#991b1b', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: '700', fontSize: '12px'}}, 'PDF')
        );
    }
    return React.createElement(
        'a',
        {href: href, target: '_blank', title: 'Datei öffnen', style: linkStyle},
        React.createElement('div', {style: {width: '56px', height: '56px', borderRadius: '10px', border: '1px solid #bfdbfe', background: '#eff6ff', color: '#1e3a8a', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: '700', fontSize: '11px'}}, 'DATEI')
    );
};


dagcomponentfuncs.SdbTitleLinkCell = function (props) {
    const row = props.data || {};
    const title = String(props.value || row.title || 'SDB');
    const href = row.pdf_url || null;
    if (!href) {
        return React.createElement(
            'span',
            {title: 'Keine PDF-Datei verfügbar. Erst PDF erzeugen.', style: {color: '#334155'}},
            title,
            React.createElement('span', {style: {marginLeft: '6px', color: '#b45309', fontSize: '11px', fontWeight: '600'}}, 'PDF fehlt')
        );
    }
    return React.createElement(
        'a',
        {
            href: href,
            target: '_blank',
            rel: 'noopener noreferrer',
            title: 'PDF öffnen',
            style: {
                color: '#2563eb',
                textDecoration: 'underline',
                textDecorationColor: '#bfdbfe',
                textUnderlineOffset: '2px',
                cursor: 'pointer'
            }
        },
        title
    );
};


dagcomponentfuncs.PercentCell = function (props) {
    const value = props.value;
    if (value === null || value === undefined || value === '') {
        return React.createElement('span', null, '');
    }
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) {
        return React.createElement('span', null, String(value));
    }
    const formatted = numberValue.toLocaleString('de-CH', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2
    });
    return React.createElement('span', null, formatted + ' %');
};
