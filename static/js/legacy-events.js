(() => {
    const allowed = new Set([
        'addTvSchedule','applyDisplayPreset','saveDisplayProfile','applyCrop','autoMatchHeights','cancelCrop','cancelUploadModal',
        'clearCrop','closeModal','closePreview','closeSettingsModal','configureBackup',
        'deleteDisplayProfile','deleteGroup','deleteImage','deleteUser','disconnectBackup','enterCropMode',
        'finishGroupMode','handleCardClick','moveGroupImage','nextSlide','openGroupPreview',
        'openSettingsModal','previousSlide','proceedUpload','refreshPreview','removeTvSchedule',
        'resetPassword','saveBackupSettings','showResetPassword','startGroupMode',
        'switchSettingsTab','testCec','toggleGalleryView','toggleImage','toggleMoreColors',
        'toggleFullscreen','togglePause','toggleScheduleDay','toggleSelect','triggerBackup','triggerFileInput','updateCropAspectLock','updateCropAspectRatio','updateCropPreviewZoom',
        'triggerRestore','ungroupFromPreview','ungroupGroup','updateGroupField',
        'updateGroupMatColor','updateGroupScale','updateGroupScalePreview','toggleDisplayProfile','updateImageField',
        'updateMatColor','updateScheduleField','updateSingleScale','updateSingleScalePreview',
        'renderSinglePreviewImage','renderSingleControls','renderGroupControls',
        'previewSingleBevel','previewGroupBevel','resetAllMatColors','switchTab'
    ]);
    const splitTopLevel = (value, delimiter) => {
        const parts = []; let current = ''; let quote = null; let depth = 0;
        for (let i = 0; i < value.length; i++) {
            const char = value[i];
            if (quote) {
                current += char;
                if (char === quote && value[i - 1] !== '\\') quote = null;
            } else if (char === "'" || char === '"') { quote = char; current += char; }
            else if (char === '(') { depth++; current += char; }
            else if (char === ')') { depth--; current += char; }
            else if (char === delimiter && depth === 0) { parts.push(current.trim()); current = ''; }
            else current += char;
        }
        if (current.trim()) parts.push(current.trim());
        return parts;
    };
    const parseArg = (token, element) => {
        token = token.trim();
        if ((token.startsWith("'") && token.endsWith("'")) || (token.startsWith('"') && token.endsWith('"'))) {
            return token.slice(1, -1).replace(/\\'/g, "'").replace(/\\"/g, '"');
        }
        if (token === 'this.value') return element.value;
        if (token === 'this.value||null') return element.value || null;
        if (token === 'this.checked') return element.checked;
        if (token === 'parseInt(this.value)') return parseInt(element.value, 10);
        if (token === 'true') return true;
        if (token === 'false') return false;
        if (token === 'null') return null;
        if (/^-?\d+(\.\d+)?$/.test(token)) return Number(token);
        throw new Error('Unsupported event argument');
    };
    const run = (source, element) => {
        if (source.includes("this.classList.toggle('open')")) {
            element.classList.toggle('open');
            element.nextElementSibling.style.display = element.classList.contains('open') ? '' : 'none';
            return;
        }
        for (const statement of splitTopLevel(source, ';')) {
            if (!statement) continue;
            if (statement === 'location.reload()') { location.reload(); continue; }
            if (statement.startsWith('this.nextElementSibling.style.display =')) {
                const sibling = element.nextElementSibling;
                sibling.style.display = sibling.style.display === 'none' ? 'flex' : 'none';
                element.textContent = sibling.style.display === 'none' ? 'More Colors' : 'Fewer Colors';
                continue;
            }
            if (statement.startsWith('this.nextElementSibling.textContent =')) {
                element.nextElementSibling.textContent = `${parseFloat(element.value).toFixed(2)}x`;
                continue;
            }
            if (statement.startsWith("this.previousElementSibling.querySelector('span').textContent")) {
                element.previousElementSibling.querySelector('span').textContent = `${element.value}px`;
                continue;
            }
            const match = statement.match(/^([A-Za-z_$][\w$]*)\((.*)\)$/s);
            if (!match || !allowed.has(match[1]) || typeof window[match[1]] !== 'function') continue;
            const args = match[2].trim() ? splitTopLevel(match[2], ',').map(arg => parseArg(arg, element)) : [];
            window[match[1]](...args);
        }
    };
    const bind = root => {
        const nodes = root.nodeType === 1 ? [root, ...root.querySelectorAll('*')] : [...document.querySelectorAll('*')];
        for (const element of nodes) {
            for (const type of ['click', 'change', 'input']) {
                const attribute = `data-on${type}`;
                if (!element.hasAttribute?.(attribute)) continue;
                const source = element.getAttribute(attribute);
                element.removeAttribute(attribute);
                element.addEventListener(type, () => run(source, element));
            }
        }
    };
    document.addEventListener('DOMContentLoaded', () => {
        bind(document);
        new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(bind)))
            .observe(document.body, {childList: true, subtree: true});
    });
})();
